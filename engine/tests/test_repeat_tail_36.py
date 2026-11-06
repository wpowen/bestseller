"""INBOX36: exact narration cleanup; speech and dependent prose stay visible."""

from pathlib import Path

import pytest

from bookengine.prose_repetition import clean_repetitions
from tools.book_checkup import check_book

FIXTURES = Path(__file__).parent / "fixtures/repeat_exit_tail_36"


def test_eval35_ch11_removes_two_narrations_and_keeps_dialogue(tmp_path):
    text = (FIXTURES / "chapter-011.md").read_text()
    result = clean_repetitions(text)
    for sentence in ("日影压到对街檐口，水仓申时封验。", "她从抽屉取出父亲航海簿的三张残页，又挑出两片页角。"):
        assert result["cleaned"].count(sentence) == 1
    assert result["cleaned"].count("那也是你爹留下的东西。") == 2
    assert not result["needs_repair"]
    (tmp_path / "chapter-011.body.md").write_text(result["cleaned"])
    report = check_book(tmp_path)["repetition"]
    assert report["narrative_repetitions"] == 0
    assert report["dialogue_repetitions"] == 1
    assert any("对白重复，可能有意" in str(r) for r in report["chapters"])


def test_eval35_ch8_password_is_reported_only():
    text = (FIXTURES / "chapter-008.md").read_text()
    result = clean_repetitions(text)
    assert result["cleaned"] == text
    assert result["counts"]["dialogue_repetitions"] == 1
    assert not result["needs_repair"]


@pytest.mark.parametrize("following", ["他忽然转身。", "这让张岚十分不安。", "那就是失踪的船。", "抬手推开了大门。"])
def test_next_dependent_sentence_is_kept_as_unknown(following):
    sentence = "张岚从东侧山谷走回了营地。"
    text = sentence + "\n\n钟声响了。" + sentence + following
    result = clean_repetitions(text)
    assert result["cleaned"] == text
    node = next(r for r in result["records"] if r["kind"] == "sentence")["occurrences"][1]
    assert node["action"] == "unknown" and node["status"] == "Unknown"
    assert not result["needs_repair"]


def test_exact_match_ignores_only_whitespace_and_deletes_empty_paragraph():
    sentence = "她从旧木箱里取出一把铁钥匙。"
    text = sentence + "\n\n灯火亮了。\n\n" + sentence.replace("旧木箱", "旧 木箱") + "\n\n屋门随风关闭。"
    assert clean_repetitions(text)["cleaned"] == sentence + "\n\n灯火亮了。\n\n屋门随风关闭。"
    variant = sentence.replace("。", "！")
    assert clean_repetitions(sentence + "\n\n" + variant)["cleaned"].endswith(variant)


@pytest.mark.parametrize("opener,closer", [("“", "”"), ("「", "」"), ("『", "』"), ('"', '"'), ("'", "'")])
def test_dialogue_in_adjacent_identical_paragraphs_is_never_removed(opener, closer):
    quote = opener + "山谷里的那座旧营地已经空了。" + closer
    text = quote + "\n\n" + quote
    result = clean_repetitions(text)
    assert result["cleaned"] == text
    assert not result["needs_repair"]


def test_quote_and_narration_are_not_each_others_deletion_reference():
    sentence = "山谷里的那座旧营地已经空了。"
    text = "“" + sentence + "”\n\n" + sentence
    assert clean_repetitions(text)["cleaned"] == text


def test_old_pronouns_do_not_block_independent_later_sentence():
    sentence = "她从旧木箱里取出一把铁钥匙。"
    text = sentence + "\n\n他站在门外。" + sentence + "院里的灯火亮了。"
    assert clean_repetitions(text)["cleaned"] == sentence + "\n\n他站在门外。院里的灯火亮了。"


def test_quotes_across_paragraphs_remain_report_only():
    sentence = "山谷里的那座旧营地已经空了。"
    text = "“" + sentence + "\n\n" + sentence + "”"
    result = clean_repetitions(text)
    assert result["cleaned"] == text
    assert result["counts"]["dialogue_repetitions"] == 1
    assert not result["needs_repair"]


def test_writer_deterministic_cleanup_precedes_extraction_without_repair_call():
    from support_minimal_b import MinimalBTests, CaptureClient, body

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        sentence = "她从旧木箱里取出一把铁钥匙。"
        text = sentence + "\n\n" + body(60) + "\n\n" + sentence + "院里的灯火亮了。"
        client = CaptureClient(fixture.out, [text])
        row = fixture.run_chapter(client)
        saved = (fixture.out / "chapter-002.body.md").read_text()
        assert saved.count(sentence) == 1
        assert not any("repetition-repair" in c["stage"] for c in client.calls)
        assert row["repetition"]["remaining"]["narrative_repetitions"] == 0
        assert next(c["prompt"] for c in client.calls if c["stage"].endswith("-finish")).count(sentence) == 1
    finally:
        fixture.doCleanups()
