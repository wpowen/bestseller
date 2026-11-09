"""INBOX54: first-use unregistered person-name findings."""

import yaml

from bookengine.cast_consistency import cast_checkup, cast_findings, repair_cast_consistency
from tools.book_checkup import check_book


def bible():
    return {
        "characters": [
            {"name": "顾奕棣", "role": "protagonist", "gender": "男", "aliases": ["阿棣"]},
            {"name": "顾晴棣", "role": "support", "gender": "女", "address": "晴棣"},
            {"name": "沈弘策", "role": "support", "gender": "女"},
        ]
    }


def test_eval52_unregistered_old_name_is_reported_once_at_first_appearance():
    text = "“阿岐。”一个陌生而熟悉的旧名落进耳中。顾奕棣没有回应。后来回声又喊：“阿岐！”"

    findings = [row for row in cast_findings(text, bible(), chapter=15) if row["kind"] == "unregistered_name"]

    assert len(findings) == 1
    assert findings[0]["name"] == "阿岐"
    assert findings[0]["status"] == "Inference"
    assert findings[0]["quote"].startswith("“阿岐")


def test_registered_names_aliases_and_addresses_are_not_reported():
    text = "“顾奕棣。”回声喊。\n\n“阿棣！”顾晴棣回头。\n\n“晴棣。”沈弘策低声叫她。"

    assert not [row for row in cast_findings(text, bible(), chapter=15) if row["kind"] == "unregistered_name"]


def test_short_dialogue_and_registered_name_followed_by_a_verb_are_not_names():
    text = "“别怕。”\n\n“能用。”\n\n沈弘策握紧药包：“时限？”\n\n顾奕棣没有回答。"

    assert not [row for row in cast_findings(text, bible(), chapter=5) if row["kind"] == "unregistered_name"]


def test_unknown_subject_name_is_detected_without_requiring_repeat_mentions():
    text = "韩远抬头看向雪坡，随后把药包塞回怀里。"

    findings = [row for row in cast_findings(text, bible(), chapter=15) if row["kind"] == "unregistered_name"]

    assert len(findings) == 1 and findings[0]["name"] == "韩远"


def test_unregistered_name_is_shared_by_chapter_repair_and_whole_book_checkup(tmp_path):
    text = "“阿岐。”一个陌生而熟悉的旧名落进耳中。"
    chapter = cast_checkup(bible(), [{"number": 15, "text": text}])
    assert chapter["counts"]["unregistered_name"] == 1

    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(bible(), allow_unicode=True))
    (tmp_path / "chapter-015.body.md").write_text(text)
    whole = check_book(tmp_path, source="draft")["cast_consistency"]
    assert whole["counts"]["unregistered_name"] == 1

    repeated = cast_checkup(
        bible(),
        [{"number": 15, "text": text}, {"number": 16, "text": "回声再一次喊：“阿岐！”"}],
    )
    assert repeated["counts"]["unregistered_name"] == 1


def test_unregistered_name_is_reported_and_left_in_the_text():
    """Policy (supervisor 2026-11-09): walk-on names and nicknames are review hints, not repairs."""
    text = "“阿岐。”一个陌生而熟悉的旧名落进耳中。"

    def ask(*_args, **_kwargs):
        raise AssertionError("unregistered names must not be sent to the model")

    repaired, _, failed, note = repair_cast_consistency(
        None,
        "chapter15",
        text,
        {"events": []},
        False,
        {},
        bible(),
        {"number": 15},
        lambda *_args, **_kwargs: ({"events": []}, False, None),
        ask,
    )

    assert repaired == text
    assert failed is False and note["status"] == "checked"
    assert [row["name"] for row in note["before"] if row["kind"] == "unregistered_name"] == ["阿岐"]


def test_nickname_derived_from_a_registered_name_is_known():
    from bookengine.unregistered_names import name_findings

    bible = dict(characters=[dict(name="顾奕棣", role="protagonist")])
    text = "风里传来母亲的声音。“阿棣。”她喊着他的名字。"
    assert [f["name"] for f in name_findings(text, bible)] == []


def test_unregistered_names_are_reported_but_never_sent_for_repair(monkeypatch):
    from bookengine import cast_consistency as cast

    finding = dict(
        kind="unregistered_name", status="Inference", name="马三根", id=1, chapter=11, quote="马三根走过来。"
    )
    monkeypatch.setattr(cast, "cast_findings", lambda text, bible, chapter=None: [finding])

    def ask(*args, **kwargs):
        raise AssertionError("an unregistered walk-on name must not trigger a model repair")

    text, raw, failed, note = cast.repair_cast_consistency(
        None, "ch11", "马三根走过来。", {}, [], {}, {}, dict(number=11), None, ask
    )
    assert text == "马三根走过来。"
    assert note["before"] == [finding]
