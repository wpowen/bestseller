"""INBOX 46: de-AI may not introduce English or structural prose."""

import json

from deai.flow import polish
from deai.hygiene import reject_introduced
from deai.text import sentences


def _edit(text, replacement):
    row = sentences(text)[0]
    return dict(
        start=row["start"],
        end=row["end"],
        before=row["text"],
        replacement=replacement,
        pid=row["pid"],
        source="model",
    )


def test_rejects_only_edits_that_introduce_english_or_structure():
    original = "这是霜族留下的旧印。\n\n她抬头看向雪坡。"
    bad = [
        _edit("这是霜族留下的旧印。", "这是 belonged to 霜族的旧印。"),
        dict(_edit("她抬头看向雪坡。", "第二场戏转入雪坡。"), start=13, end=22, pid="P2"),
    ]

    candidate, kept, warnings = reject_introduced(original, bad)

    assert candidate == original
    assert kept == []
    assert [row["types"] for row in warnings] == [["english"], ["structure"]]
    assert all(row["rollback"] == "text_hygiene" for row in warnings)


def test_existing_english_can_be_removed_by_deai():
    original = "狭窄处/low石面刮过他的肩背。"
    edit = _edit(original, "狭窄处低矮石面刮过他的肩背。")

    candidate, kept, warnings = reject_introduced(original, [edit])

    assert candidate == "狭窄处低矮石面刮过他的肩背。"
    assert kept == [edit] and warnings == []


class HygieneClient:
    def __init__(self):
        self.calls = []

    def __call__(self, messages, *, stage, **kwargs):
        data = json.loads(messages[-1]["content"].split("\n")[-1])
        self.calls.append(dict(stage=stage, data=data, usage={}))
        if "targets" in data:
            return {
                "candidates": [
                    dict(sid=row["sid"], choices=["屋里 belonged to 黑暗，没有灯。"]) for row in data["targets"]
                ]
            }
        if "items" in data:
            return {"checks": [dict(id=row["id"], ok=True, reason="fixture") for row in data["items"]]}
        return dict(checked_pids=[row["pid"] for row in data["paragraphs"]], facts=[], chapter={})


def test_polish_keeps_original_sentence_and_records_hygiene_rollback():
    client = HygieneClient()
    original = "屋里阴森，没有灯。"

    result = polish(original, {}, client)

    assert result.text == original and result.edits == []
    assert result.status == "unchanged"
    assert any(row.get("rollback") == "text_hygiene" and row["types"] == ["english"] for row in result.warnings)
    assert not any("items" in call["data"] for call in client.calls)
