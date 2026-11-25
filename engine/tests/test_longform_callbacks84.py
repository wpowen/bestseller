"""Cross-volume callbacks use bounded history and literal passages in the packed writer request."""

import pytest

from bookengine.config import Book
from bookengine.craft import recall_events
from bookengine.longutil import ask
from bookengine.q1_draft_prompt import production_prompt
from bookengine.q1_plan import callbacks
from bookengine.storage import save
from support_minimal_b import CaptureClient

PASSAGE = "主角拆下门钉，见钉眼里面有一道蓝漆。她没动，让漆片留在原处。"
SOURCE = "见钉眼里面有一道蓝漆。"
NEXT_CHAPTER = 45


def _completed(out, number, text, **row):
    save(out / f"chapters/{number:03d}.json", dict(number=number, status="complete", fact_source="final_body", **row))
    save(out / f"chapter-{number:03d}.body.md", text)


def test_volume_summaries_do_not_hide_saved_chapter_events(tmp_path):
    _completed(tmp_path, 4, "她把一颗铜扣交还船主。", summary="她把一颗铜扣交还船主。")
    _completed(tmp_path, 37, PASSAGE, summary=SOURCE)
    records = [dict(number=4, summary="她把一颗铜扣交还船主。")]
    volumes = [
        dict(number=1, start=1, end=20, summary="第一卷梗概"),
        dict(number=2, start=21, end=40, summary="第二卷梗概"),
    ]
    expected = recall_events(tmp_path, records, 48, 120, 50)
    recalled = recall_events(tmp_path, volumes, 48, 120, 50)
    assert expected and expected[0]["original_excerpt"] == "她把一颗铜扣交还船主。"
    assert any(item["chapter"] == 37 and item["original_excerpt"] == PASSAGE for item in recalled)
    assert {item["chapter"] for item in recalled} == {4, 37}
    assert "第二卷梗概" not in str(recalled)


def test_callbacks_ignore_current_and_future_stages_when_bounded():
    history = [dict(chapter=n, summary=f"发生{n}") for n in (30, 40, 42, NEXT_CHAPTER, 75)]
    assert callbacks(history, before=41) == [
        dict(chapter=30, event="发生30"),
        dict(chapter=40, event="发生40"),
    ]


@pytest.mark.parametrize("writer_input", ["full", "compact"])
def test_midvolume_callback_reaches_packed_writer_without_future_or_unverified_sources(tmp_path, writer_input):
    _completed(tmp_path, 37, "闲笔写蓝漆在窗边。\n\n" + PASSAGE, summary=SOURCE)
    _completed(tmp_path, 42, "这是同卷以后才知道的证据。")
    _completed(tmp_path, 50, "终章秘密不能提前泄露。")
    history = [dict(number=2, start=21, end=40, summary="推断的人物身份不能写给写手")]
    outline = dict(
        number=NEXT_CHAPTER,
        scenes=[dict(characters=["主角", "外来人"], action="两人回到旧门边。")],
        callback_events=[
            dict(chapter=37, source_quote=SOURCE, event="不能把规划猜测传给写手"),
            dict(chapter=37, original_excerpt=PASSAGE[:25], event="旧缓存里的推断不应传给写手"),
            dict(chapter=37, source_quote="蓝漆指认船主为凶手"),
            dict(chapter=42, source_quote="同卷以后才知道"),
            dict(chapter=50, source_quote="终章秘密不能提前泄露"),
        ],
    )
    book = Book("xuanhuan-power-fantasy", 120, 2200, "", writer_input=writer_input)
    vol = dict(number=3, start=41, end=60)
    instruction, sections = production_prompt(
        book, {}, {}, vol, outline, {}, history, [], "", [], tmp_path, "b", {"b": ""}
    )
    fake = CaptureClient(tmp_path, ["正文"])
    ask(fake, "r2-ch45-draft", instruction, sections, input_limit=10000)
    prompt = fake.calls[0]["prompt"]
    assert prompt.count(PASSAGE) == 1
    assert "第37章原文" in prompt
    assert "人物只能根据此前亲历或正文已经交代的转述" in prompt
    assert "本章先获取" in prompt
    assert "闲笔" not in prompt
    assert "规划猜测" not in prompt
    assert "旧缓存里的推断" not in prompt
    assert "推断的人物身份" not in prompt
    assert "蓝漆指认船主" not in prompt
    assert "同卷以后才知道" not in prompt
    assert "终章秘密" not in prompt


def test_outlines_preserve_the_named_reference_at_midvolume(tmp_path):
    import json

    from bookengine import q1_plan
    from test_q1_plan import StageFixture

    _completed(tmp_path, 37, PASSAGE, summary=SOURCE)
    reference = dict(chapter=37, source_quote=SOURCE)
    fixture = StageFixture(json.dumps([dict(number=NEXT_CHAPTER, callback_events=[reference])], ensure_ascii=False))
    fixture.out = tmp_path
    volumes = [dict(number=2, start=21, end=40), dict(number=3, start=41, end=60)]
    bible = dict(chapters=120, volumes=volumes)
    outlines = q1_plan.outlines(
        fixture,
        bible,
        {},
        q1_plan.DATA["profile"],
        volumes[-1],
        NEXT_CHAPTER,
        NEXT_CHAPTER,
        {},
        [],
        [dict(volumes[0], summary="第二卷梗概")],
        [],
    )
    assert outlines[0]["callback_events"] == [reference]
    assert any("跨卷回调候选" in prompt and SOURCE in prompt for _, prompt, _ in fixture.calls)
    calls_before = len(fixture.calls)
    restored = q1_plan.outlines(
        fixture,
        bible,
        {},
        q1_plan.DATA["profile"],
        volumes[-1],
        NEXT_CHAPTER,
        NEXT_CHAPTER,
        {},
        [],
        [dict(volumes[0], summary="第二卷梗概")],
        [],
    )
    assert restored[0]["callback_events"] == [reference]
    assert len(fixture.calls) == calls_before


def test_due_callback_reads_the_recorded_early_plant_instead_of_the_volume_summary(tmp_path):
    from bookengine.chapter_callbacks import outline_callbacks, planning_callbacks

    _completed(tmp_path, 4, PASSAGE, summary=SOURCE)
    _completed(tmp_path, 37, "卷末只有山风。", summary="卷末只有山风。")
    save(tmp_path / "story_ledger.json", {"duties": {"door-plant": dict(status="done", chapter=4, quote=SOURCE)}})
    history = [dict(number=1, start=1, end=20), dict(number=2, start=21, end=40)]
    design = {"foreshadows": [dict(id="door", payoff=NEXT_CHAPTER)]}
    candidates = planning_callbacks(tmp_path, history, 41, design, 44, 46)
    assert candidates[0] == dict(chapter=4, source_quote=SOURCE, due=[NEXT_CHAPTER])
    assert outline_callbacks(dict(number=NEXT_CHAPTER), candidates, 120, 60) == candidates[:1]


def test_writer_omits_an_oversized_callback_whole_and_preserves_the_next_one(tmp_path):
    large = "大段旧史。" * 800 + "旧钥匙属于主角。"
    _completed(tmp_path, 36, large)
    _completed(tmp_path, 37, PASSAGE)
    outline = dict(
        number=NEXT_CHAPTER,
        callback_events=[dict(chapter=36, source_quote="旧钥匙属于主角。"), dict(chapter=37, source_quote=SOURCE)],
    )
    book = Book("xuanhuan-power-fantasy", 120, 2200, "", writer_input="compact")
    instruction, sections = production_prompt(
        book, {}, {}, dict(number=3, start=41, end=60), outline, {}, [], [], "", [], tmp_path, "b", {"b": ""}
    )
    fake = CaptureClient(tmp_path, ["正文"])
    fake.context_limit = 2000
    drops = []
    ask(fake, "r2-ch45-draft", instruction, sections, input_limit=10000, writer_drops=drops)
    prompt = fake.calls[0]["prompt"]
    assert PASSAGE in prompt
    assert "大段旧史" not in prompt
    assert drops == ["压缩缩进", "本章点名的跨卷回调原文（第36章）"]
