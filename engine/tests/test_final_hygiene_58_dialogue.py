"""INBOX 58 item 5: repeated dialogue is repaired across a whole chapter."""

import json

import yaml

from bookengine.dialogue_hygiene import dialogue_repetition_hits, repair_dialogue_repetitions
from tools.book_checkup import check_book


F57_CH7 = """火房管事裴九把最后一捆备用炭推给顾和笙：“顾领队，北坡还等着炭。”

顾和笙将令牌塞进怀里。

顾和笙抬起头，脸色仍旧冷硬：“先把活人送上去。炭车全走北坡，沈青萝跟我查韩砺最后运炭的账。”

账册摊开在地上。

顾和笙抬起头，脸色仍旧冷硬：“先把活人送上去。炭车全走北坡，沈青萝跟我查韩砺最后运炭的账。”他顿了顿，右手仍扣着令牌。"""


def test_f57_fourth_trial_nonadjacent_dialogue_is_detected():
    hits = dialogue_repetition_hits(F57_CH7, {}, {"number": 7})

    assert len(hits) == 1
    assert hits[0]["kind"] == "exact_dialogue"
    assert hits[0]["text"] == "先把活人送上去。炭车全走北坡，沈青萝跟我查韩砺最后运炭的账。"


def test_near_dialogue_is_detected_beyond_adjacent_paragraphs():
    text = "“你先带伤员走北门，我随后就到。”\n\n众人搬开木栅。\n\n火势压近。\n\n“你先带伤员走北门，我很快就到。”"

    hits = dialogue_repetition_hits(text, {}, {"number": 8})

    assert len(hits) == 1
    assert hits[0]["kind"] == "near_dialogue" and hits[0]["similarity"] >= 0.84


def test_scheduled_catchphrase_is_exempt_but_unscheduled_repeat_is_not():
    text = "“先活着再说。”\n\n众人继续赶路。\n\n“先活着再说。”"
    bible = {"characters": [{"name": "顾和笙", "catchphrase": "先活着再说。"}]}

    assert dialogue_repetition_hits(text, bible, {"number": 7, "catchphrase_due": ["顾和笙"]}) == []
    assert dialogue_repetition_hits(text, bible, {"number": 7, "catchphrase_due": []})


def test_dialogue_repair_rewrites_only_later_lines_and_rechecks():
    calls = []

    def ask(*args, **kwargs):
        calls.append(args[1])
        targets = json.loads(dict(args[3])["重复对白"])
        return {"edits": [{"id": row["id"], "text": "“都按刚才的次序动，沈青萝留下查账。”"} for row in targets]}

    result, raw, failed, note = repair_dialogue_repetitions(
        None,
        "r2-ch7",
        F57_CH7,
        {"events": []},
        False,
        {},
        {},
        {"number": 7},
        lambda *_args, **_kwargs: ({"events": [], "state": {}}, False, 0),
        ask,
    )

    assert calls == ["r2-ch7-dialogue-repetition"]
    assert not dialogue_repetition_hits(result, {}, {"number": 7})
    assert result.count("先把活人送上去") == 1
    assert "他顿了顿，右手仍扣着令牌" in result
    assert raw == {"events": [], "state": {}} and failed is False
    assert note["status"] == "repaired" and len(note["edits"]) == 1


def test_checkup_registers_dialogue_repetition(tmp_path):
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump({"characters": []}, allow_unicode=True))
    (tmp_path / "run.json").write_text(json.dumps({"status": "complete", "book": {"chapters": 7}}))
    (tmp_path / "chapter-007.body.md").write_text(F57_CH7)

    result = check_book(tmp_path, source="draft")["dialogue_repetitions"]

    assert result["count"] == 1
    assert result["chapters"] == [7]
