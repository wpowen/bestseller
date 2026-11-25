from copy import deepcopy

import pytest

from bookengine.choice_cost import HEADING, choice_cost_context
from bookengine.storage import load, save
from support_minimal_b import CaptureClient, MinimalBTests as WriterFixture, body


ACT = "阿宁攥住陆七的袖子把他拖出渡口"
COST = "渡桥当即坍塌。陆七扶住了她"
FORK = "若烧断缆索，渡桥便会坍塌；若守住桥，陆七就会被水冲走。"


def review():
    return dict(
        status="repaired",
        previous_chapter=1,
        before=dict(kind="skipped_choice", prior_choice=FORK),
        after=dict(status="clear", preserves_facts=True, new_action_quote=ACT, consequence_quote=COST),
    )


def next_scene():
    return dict(
        number=3,
        scenes=[
            dict(
                where="城外渡桥边",
                characters=["阿宁", "陆七"],
                goal="沿渡桥返回城门",
                obstacle="沿路封锁",
                action="查看便道",
                choice="绕到后门",
                result="两人进城",
            )
        ],
    )


@pytest.mark.parametrize(
    "variant",
    [
        "unfixed",
        "clipped",
        "rollback",
        "unverified",
        "failed",
        "not_adjacent",
        "another_view",
        "unrelated",
        "just_place",
        "just_people",
    ],
)
def test_unverified_or_unrelated_costs_do_not_become_plot(variant, tmp_path):
    note = review()
    chapter = dict(
        number=2,
        status="complete",
        fact_source="final_body",
        polish=dict(plot_progression=dict(adjacent_semantic=note)),
    )
    text = ACT + "。" + COST + "。"
    outline = next_scene()
    if variant == "unfixed":
        chapter["polish"] = {}
    elif variant == "clipped":
        text = text.replace(COST, "两人靠着墙休息。")
    elif variant == "rollback":
        note["status"] = "rolled_back"
    elif variant == "unverified":
        note["after"]["preserves_facts"] = False
    elif variant == "failed":
        chapter["state_failed"] = True
    elif variant == "not_adjacent":
        chapter["number"] = 1
    elif variant == "another_view":
        outline["scenes"][0]["pov"] = "陆七"
    elif variant == "just_place":
        outline["scenes"][0]["goal"] = "阿宁带陆七看山花"
    else:
        outline["scenes"][0].update(where="山顶", goal="商量旧事" if variant == "unrelated" else "陆七扶住了她")
    save(tmp_path / "chapter-002.body.md", text)
    assert choice_cost_context(tmp_path, chapter, text, outline) == []


def test_normal_chapter_stores_review_and_next_writer_reads_the_saved_fact(monkeypatch):
    fixture = WriterFixture()
    fixture.setUp()
    try:
        fixture.bible["characters"].append(dict(name="陆七", role="support"))
        draft = body(60, total=170) + "\n\n" + ACT + "，" + COST + "。"

        # The adjacent reviewer has its own real-repair and rollback tests. Keep its real outgoing data shape.
        def accepted(_client, _prefix, text, raw, failed, *_args, **_kwargs):
            assert ACT in text and COST in text
            return text, raw, failed, {"status": "checked", "adjacent_semantic": review()}

        monkeypatch.setattr("bookengine.q1_chapter.repair_plot_replay", accepted)
        row = fixture.run_chapter(CaptureClient(fixture.out, [draft]))
        saved = load(fixture.out / "chapters/002.json")
        assert saved == row and saved["polish"]["plot_progression"]["adjacent_semantic"] == review()

        class Seen(Exception):
            pass

        capture = CaptureClient(fixture.out, [], {"r2-ch3-draft": Seen()})
        outline = dict(deepcopy(fixture.outline), **next_scene())
        with pytest.raises(Seen):
            fixture.run_chapter(capture, row["state"], [saved], outline)
        packed = capture.calls[0]["prompt"]
        section = packed.split(HEADING, 1)[1].split("\n\n##", 1)[0]
        assert ACT in section and COST in section and "沿渡桥返回城门" in section and "已解除或无关时略过" in section
    finally:
        fixture.doCleanups()
