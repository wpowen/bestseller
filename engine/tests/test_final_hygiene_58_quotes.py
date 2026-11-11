"""INBOX 58 item 4: deterministic per-paragraph Chinese quote repair."""

import json

import yaml

from bookengine.quote_text import repair_unclosed_quotes, unclosed_quote_hits
from tools.book_checkup import check_book


EVAL57_CH24 = (
    "“药烟最多维持一炷香。”白苓将药囊递给顾立谦，“群兽接近时，药量由我掌握。"
    "你不能借霜团的身法去追，也不能让它反复开鼻。烟尽之前，你必须亲手收束借力。"
    "若你分不清自己是在借力，还是在逃，我会让霜团咬断你的腕脉。"
)

EVAL57_CH38 = (
    "片刻后，陆铜生的声音沿钟井传下：“西槽空！北巷满槽！旧吏说，只有他的押字才能停。可他的印已经压不住任何东西了！"
)


def test_eval57_chapters_24_and_38_each_have_one_unclosed_quote():
    assert len(unclosed_quote_hits(EVAL57_CH24)) == 1
    assert len(unclosed_quote_hits(EVAL57_CH38)) == 1
    assert all(row["missing"] == "”" for text in (EVAL57_CH24, EVAL57_CH38) for row in unclosed_quote_hits(text))


def test_quote_repair_adds_only_delimiter_and_never_changes_dialogue_text():
    for text in (EVAL57_CH24, EVAL57_CH38):
        result, raw, failed, note = repair_unclosed_quotes(
            None,
            "r2-ch24",
            text,
            {"events": []},
            False,
            {},
            {},
            {"number": 24},
            lambda *_args, **_kwargs: ({"events": [], "state": {}}, False, 0),
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model must not be called")),
        )

        assert not unclosed_quote_hits(result)
        assert result.replace("“", "").replace("”", "") == text.replace("“", "").replace("”", "")
        assert raw == {"events": [], "state": {}} and failed is False
        assert note["status"] == "repaired" and len(note["edits"]) == 1


def test_quote_repair_rolls_back_ambiguous_multiple_missing_delimiters():
    text = "“第一句。又说：“第二句。"

    result, raw, failed, note = repair_unclosed_quotes(
        None,
        "r2-ch1",
        text,
        {"events": []},
        False,
        {},
        {},
        {"number": 1},
        lambda *_args, **_kwargs: ({"events": []}, False, 0),
        None,
    )

    assert result == text and raw == {"events": []} and failed is False
    assert note["status"] == "rolled_back" and note["reason"] == "ambiguous_quote_balance"


def test_checkup_counts_unclosed_quotes_by_chapter(tmp_path):
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump({"characters": []}, allow_unicode=True))
    (tmp_path / "run.json").write_text(json.dumps({"status": "complete", "book": {"chapters": 38}}))
    (tmp_path / "final").mkdir()
    (tmp_path / "final/chapter-024.md").write_text(EVAL57_CH24)
    (tmp_path / "final/chapter-038.md").write_text(EVAL57_CH38)

    result = check_book(tmp_path)["unclosed_quotes"]

    assert result["count"] == 2
    assert result["chapters"] == [24, 38]
