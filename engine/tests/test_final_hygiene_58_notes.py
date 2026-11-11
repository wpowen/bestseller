"""INBOX 58 item 1: model notes must never become final prose."""

import json

import pytest

from bookengine.text_hygiene import (
    ENGLISH_WORD_GATE,
    inspect_text,
    model_note_hits,
    repair_text,
    require_publishable,
)
from exporter import export_book


EVAL57_EXCERPT = """陆铜生立刻 drove black seal cart to break? "一车冲来". 顾立谦引开雪豹. Need concrete.

Need make second scene maybe around 1000 chars. Ensure "碾压", no new injury beyond max one. Avoid additional leg arrow. We can say arrow hit wood.

Snow leopard power anger. Need active restraint. He stands ahead, lures away. Forest team.

Let's craft around 2200 Chinese chars. Ensure two scenes only. Use blank line only.

Let's estimate. Need direct正文 no title.</mm:think>车队离开冰面时，顾立谦最后一个回望了一眼。"""


def test_eval57_notes_and_eval39_surviving_fragments_are_detected_as_notes():
    hits = model_note_hits("陆铜生喊了一声。\n\n" + EVAL57_EXCERPT + "\n\n霜团贴着他的左腿。")

    assert len(hits) == 1
    assert "Need concrete" in hits[0]["text"]
    assert "车队离开冰面" not in hits[0]["text"]
    assert inspect_text(EVAL57_EXCERPT)["counts"]["model_notes"] == 1
    assert model_note_hits("Need Chinese only. Scene1 emotional. Ensure 300 movement, then write正文。")


def test_english_gate_uses_eval52_eval57_calibrated_threshold():
    assert ENGLISH_WORD_GATE == 12
    assert inspect_text("他把 iPhone 收回口袋。\n\n守卫退到A12号位。")["counts"]["english_words"] == 1
    assert inspect_text("他把 iPhone 收回口袋。")["counts"]["english_gate_blocks"] == 0
    blocked = " ".join(f"word{number}" for number in range(ENGLISH_WORD_GATE + 1))
    assert inspect_text(blocked)["counts"]["english_gate_blocks"] == 1


def test_note_repair_deletes_notes_instead_of_translating_and_bridges_only_the_gap():
    text = "陆铜生驾车撞向断栅。\n\n" + EVAL57_EXCERPT + "\n\n霜团贴着顾立谦的左腿。"
    calls = []

    def ask(*args, **kwargs):
        calls.append(args[1])
        if args[1].endswith("-model-note-gap"):
            return {"text": "车轮撞断木栅，众人趁着缺口撤入排洪道。"}
        raise AssertionError(args[1])

    result, raw, failed, note = repair_text(
        None,
        "r2-ch27",
        text,
        {"events": []},
        False,
        {},
        {"characters": [{"name": "顾立谦"}, {"name": "陆铜生"}, {"name": "霜团"}]},
        {"number": 27},
        lambda *_args, **_kwargs: ({"events": [], "state": {}}, False, 0),
        ask,
    )

    assert calls == ["r2-ch27-model-note-gap"]
    assert "Need" not in result and "drove" not in result and "第二场" not in result
    assert "车队离开冰面" in result and "车轮撞断木栅" in result
    assert not model_note_hits(result)
    assert inspect_text(result)["counts"]["english_gate_blocks"] == 0
    assert raw == {"events": [], "state": {}} and failed is False
    assert note["model_notes"]["status"] == "repaired"


def test_unresolved_model_note_marks_chapter_unfinalized_and_stops(tmp_path):
    with pytest.raises(RuntimeError, match="chapter 27.*model note"):
        require_publishable(EVAL57_EXCERPT, 27, tmp_path)

    marker = json.loads((tmp_path / "chapters/027.unfinalized.json").read_text())
    assert marker["status"] == "unfinalized"
    assert marker["chapter"] == 27
    assert not (tmp_path / "chapter-027.body.md").exists()


def test_export_refuses_final_chapter_with_model_notes_and_names_chapter(tmp_path):
    (tmp_path / "final").mkdir()
    (tmp_path / "chapters").mkdir()
    (tmp_path / "run.json").write_text(json.dumps({"book": {"chapters": 1}}))
    (tmp_path / "book.md").write_text("# 测试书\n")
    (tmp_path / "chapters/001.json").write_text(json.dumps({"title": "坏终稿"}))
    (tmp_path / "final/chapter-001.md").write_text(EVAL57_EXCERPT)
    (tmp_path / "final/chapter-001.deai.json").write_text(json.dumps({"status": "unchanged", "complete": True}))

    with pytest.raises(ValueError, match="chapter 1.*model note"):
        export_book(tmp_path, formats=("txt",))

    assert not (tmp_path / "exports").exists()
