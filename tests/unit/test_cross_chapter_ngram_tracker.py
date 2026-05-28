from bestseller.services.cross_chapter_ngram_tracker import (
    compute_ngram_overuse_from_chapters,
    render_ngram_avoidance_block,
)


def test_ngram_above_hot_threshold_marked_overused():
    report = compute_ngram_overuse_from_chapters(
        [(1, "林渊盯着铜钱。林渊盯着门。"), (2, "林渊盯着镜子。林渊盯着账页。")],
        hot_threshold=4,
    )

    assert any(item.ngram == "林渊盯着" for item in report.overused)


def test_rising_ngram_uses_last_five_chapters_window():
    chapters = [(1, "旧词旧词旧词旧词"), (2, "铜钱边缘"), (3, "铜钱边缘"), (4, "铜钱边缘")]
    report = compute_ngram_overuse_from_chapters(
        chapters,
        hot_threshold=10,
        rising_threshold=3,
    )

    assert any(item.ngram == "铜钱边缘" for item in report.rising)


def test_proper_nouns_excluded_when_flag_true():
    report = compute_ngram_overuse_from_chapters(
        [(1, "林渊林渊林渊林渊")],
        hot_threshold=2,
        excluded_ngrams={"林渊"},
        min_ngram=2,
        max_ngram=2,
    )

    assert all(item.ngram != "林渊" for item in report.overused)


def test_render_block_contains_top_overused_phrases():
    report = compute_ngram_overuse_from_chapters(
        [(1, "林渊盯着铜钱。林渊盯着门。")],
        hot_threshold=2,
    )

    assert "林渊盯着" in render_ngram_avoidance_block(report)


def test_empty_corpus_returns_safe_count_zero():
    report = compute_ngram_overuse_from_chapters([])

    assert report.safe_count == 0
    assert report.overused == ()


def test_render_dedupes_overlapping_ngrams_with_same_count():
    """A 5-gram with the same count as its 3/4-gram substrings should
    suppress the shorter forms in the rendered prompt."""
    report = compute_ngram_overuse_from_chapters(
        [(i, "林渊盯着铜钱沿。" * 4) for i in range(1, 6)],
        hot_threshold=4,
        min_ngram=3,
        max_ngram=5,
    )

    block = render_ngram_avoidance_block(report)

    # The longest 5-gram should be present
    assert "林渊盯着铜" in block
    # Strict substring with identical count should be dropped
    assert '"林渊盯"' not in block
    assert '"林渊盯着"' not in block
    # Count claim should refer to the longest cluster
    lines = [line for line in block.splitlines() if "林渊盯着铜" in line]
    assert lines, "expected the longest ngram to appear in the rendered block"


def test_render_keeps_overlap_when_counts_differ():
    """When a shorter ngram has STRICTLY more occurrences than the longer
    one (because it appears in other contexts too), both must be kept."""
    text_a = "林渊跑过去。林渊跑过去。林渊跑过去。林渊跑过去。林渊跑过去。"
    text_b = "林渊盯着铜钱沿。" * 4
    report = compute_ngram_overuse_from_chapters(
        [(1, text_a), (2, text_b), (3, text_b)],
        hot_threshold=4,
        min_ngram=2,
        max_ngram=4,
    )

    block = render_ngram_avoidance_block(report)

    # 林渊 appears more times than 林渊盯 because it's in both texts
    assert "林渊" in block
