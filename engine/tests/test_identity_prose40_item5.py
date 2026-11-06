"""INBOX40 item 5: exact identity evidence and guarded opening repair, offline."""

import hashlib
import json
from pathlib import Path

import pytest

from bookengine.prose_similarity import analyze_prose
from bookengine.q1_repetition import repair_repetitions


FIXTURE = Path(__file__).parent / "fixtures/followup40_item5"
TEXT = (FIXTURE / "trial50-001.md").read_text()
BIBLE = json.loads((FIXTURE / "bible.json").read_text())
CARD = "他是被逐出驭兽宗的矿镇少年，也是雪耳的契主。"
ACTION = "顾冬岚扯下逐他出门的驭兽宗旧徽，掌心契印亮起，雪耳立即伏低身子。"


def cards(result):
    return [r for r in result["records"] if r["kind"] == "card_explanation"]


def repair(text=TEXT, *, chapter=1, bible=BIBLE, edits=True, invalid=False):
    requests = []

    def ask(*args, **kwargs):
        requests.append(args[1])
        hits = json.loads(dict(args[3])["可修改的句S编号"])
        assert [h["text"] for h in hits] == [CARD]
        return {"edits": [dict(id=hits[0]["id"], text=ACTION)] if edits else []}

    def extract(*args, **kwargs):
        requests.append(args[1])
        assert args[2] == text.replace(CARD, ACTION)
        return {"events": [], "summary": "offline recheck"}, invalid, 0

    result = repair_repetitions(None, "ch1", text, {}, False, {}, bible, {"number": chapter}, extract, ask)
    return result, requests


def test_real_chapter_fixture_reports_one_card_with_exact_identity_evidence():
    assert hashlib.sha256((FIXTURE / "trial50-001.md").read_bytes()).hexdigest() == BIBLE["provenance"]["source_sha256"]
    result = analyze_prose(TEXT, BIBLE)
    assert result["counts"]["card_explanations"] == 1
    card = cards(result)[0]
    assert card["paragraph"] == 1 and card["text"] == CARD
    assert TEXT[card["start"] : card["end"]] == CARD
    matches = card["identity_matches"]
    assert matches and matches[0]["character"] == "顾冬岚"
    for match in matches:
        assert len(match["quote"]) >= 8
        assert match["quote"] in match["identity"]
        assert TEXT[match["start"] : match["end"]] == match["quote"]
    # Chapter-specific first-paragraph routing belongs to repair_repetitions.
    assert not result["needs_repair"]


@pytest.mark.parametrize("subject", ["他", "她"])
def test_is_also_sentence_is_detected_without_bible_and_only_once(subject):
    text = subject + "是外乡来的赶车人，也是山里孩子的先生。"
    assert analyze_prose(text)["counts"]["card_explanations"] == 1


@pytest.mark.parametrize("length, expected", [(7, 0), (8, 1), (11, 1)])
def test_identity_match_threshold_uses_continuous_literal_characters(length, expected):
    identity = "旧渡口最后一位守灯人"
    bible = {"characters": [{"name": "许遥", "identity": identity}]}
    result = analyze_prose("许遥把" + identity[:length] + "的旧印放在桌上。", bible=bible)
    assert result["counts"]["card_explanations"] == expected


@pytest.mark.parametrize("text", ["旧渡口最后，一位守灯人。", "旧渡口最后。\n\n一位守灯人。"])
def test_identity_does_not_bridge_punctuation_or_paragraphs(text):
    bible = {"characters": [{"name": "许遥", "identity": "旧渡口最后一位守灯人"}]}
    assert not cards(analyze_prose(text, bible))


def test_one_sentence_with_several_identity_matches_counts_once():
    bible = {
        "characters": [
            {"name": "许遥", "identity": "旧渡口最后一位守灯人"},
            {"name": "陶宁", "identity": "北城里最年轻的修伞匠"},
        ]
    }
    text = "旧渡口最后一位守灯人和北城里最年轻的修伞匠一同推开门。"
    found = cards(analyze_prose(text, bible))
    assert len(found) == 1
    assert {m["character"] for m in found[0]["identity_matches"]} == {"许遥", "陶宁"}


@pytest.mark.parametrize("opener, closer", [("“", "”"), ("「", "」"), ('"', '"'), ("『", "』")])
def test_identity_in_dialogue_stays_out_of_card_repair(opener, closer):
    assert not cards(analyze_prose(opener + CARD + closer, BIBLE))


def test_optional_bible_preserves_old_call_and_ignores_noncharacter_identity():
    text = "她把木匣放在桌上，起身关了窗。"
    assert analyze_prose(text) == analyze_prose(text, None) == analyze_prose(text, {})
    assert not cards(analyze_prose("旧渡口最后一位守灯人。", {"identity": "旧渡口最后一位守灯人"}))


@pytest.mark.parametrize("bible", [BIBLE, {}])
def test_single_opening_card_enters_existing_repair_and_recheck(bible):
    (actual, checked, failed, note), requests = repair(bible=bible)
    assert actual == TEXT.replace(CARD, ACTION)
    assert not failed and checked["summary"] == "offline recheck"
    assert note["status"] == "repaired"
    assert note["before"]["card_explanations"] == 1
    assert note["after"]["card_explanations"] == 0
    assert requests == ["ch1-repetition-repair", "ch1-repetition-recheck"]


@pytest.mark.parametrize("chapter, prefix", [(2, ""), (1, "屋外下着雨。\n\n")])
def test_single_card_outside_first_chapter_first_paragraph_is_report_only(chapter, prefix):
    text = prefix + TEXT
    (actual, _, _, note), requests = repair(text, chapter=chapter)
    assert actual == text and not requests
    assert note["status"] == "checked" and note["before"]["card_explanations"] == 1


@pytest.mark.parametrize("edits, invalid", [(False, False), (True, True)])
def test_opening_repair_rolls_back_empty_edits_or_failed_recheck(edits, invalid):
    (actual, checked, failed, note), requests = repair(edits=edits, invalid=invalid)
    assert actual == TEXT and checked == {} and failed is False
    assert note["status"] == "rolled_back"
    assert len(requests) == (2 if edits else 1)


def test_candidate_counts_keep_bible_and_reject_unchanged_literal_identity():
    text = "旧渡口最后一位守灯人关了门。"
    bible = {"characters": [{"name": "许遥", "identity": "旧渡口最后一位守灯人"}]}
    requests = []

    def ask(*args, **kwargs):
        requests.append(args[1])
        return {"edits": [{"id": 1, "text": "旧渡口最后一位守灯人推开门。"}]}

    def extract(*args, **kwargs):
        pytest.fail("unchanged identity count must be rejected before extraction")

    actual, _, _, note = repair_repetitions(None, "ch1", text, {}, False, {}, bible, {"number": 1}, extract, ask)
    assert requests == ["ch1-repetition-repair"]
    assert actual == text and note["status"] == "rolled_back"
