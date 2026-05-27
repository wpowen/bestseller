# ruff: noqa: RUF001

from bestseller.services.chapter_first_sentence_diversity_gate import (
    check_first_sentence_diversity,
    extract_first_sentence,
)


def test_extract_first_sentence_skips_heading():
    text = "# 第十五章 旧账\n\n这一刻，所有线索都被压回同一条账路上。"

    assert extract_first_sentence(text) == "这一刻，所有线索都被压回同一条账路上。"


def test_first_sentence_diversity_rejects_exact_repeat():
    result = check_first_sentence_diversity(
        current_first_sentence="这一刻，所有线索都被压回同一条账路上。",
        recent_first_sentences={10: "这一刻，所有线索都被压回同一条账路上。"},
    )

    assert not result.passed
    assert result.matched_chapter == 10


def test_first_sentence_diversity_allows_distinct_opening():
    result = check_first_sentence_diversity(
        current_first_sentence="苏婉宁把封存袋压在桌面上，灯光从编号边缘滑过去。",
        recent_first_sentences={10: "这一刻，所有线索都被压回同一条账路上。"},
    )

    assert result.passed
