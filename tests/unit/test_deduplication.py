from __future__ import annotations

import pytest

from bestseller.services.deduplication import (
    build_opening_diversity_block,
    build_overused_phrase_avoidance_block,
    check_hook_repetition,
    check_opening_diversity,
    check_scene_duplication,
    clean_meta_text_markers,
    compute_jaccard_similarity,
    detect_intra_chapter_repetition,
    extract_frequent_phrases,
    remove_intra_chapter_duplicates,
    remove_intra_chapter_duplicates_paraphrase,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Jaccard similarity
# ---------------------------------------------------------------------------

def test_identical_texts_high_similarity() -> None:
    text = "这是一段很长的测试文本，用来验证相似度计算是否正确工作"
    assert compute_jaccard_similarity(text, text) == 1.0


def test_completely_different_texts_low_similarity() -> None:
    text_a = "The quick brown fox jumps over the lazy dog repeatedly"
    text_b = "An entirely unrelated sentence about submarines and rockets"
    sim = compute_jaccard_similarity(text_a, text_b)
    assert sim < 0.3


def test_empty_text_zero_similarity() -> None:
    assert compute_jaccard_similarity("", "some text") == 0.0
    assert compute_jaccard_similarity("some text", "") == 0.0
    assert compute_jaccard_similarity("", "") == 0.0


# ---------------------------------------------------------------------------
# check_scene_duplication
# ---------------------------------------------------------------------------

def test_duplicate_scene_critical() -> None:
    original = "This is a test scene with some interesting content about characters and their adventures in the world."
    findings = check_scene_duplication(
        original,
        [(1, 1, original)],  # exact copy
        warning_threshold=0.6,
        critical_threshold=0.85,
    )
    assert len(findings) == 1
    assert findings[0]["severity"] == "critical"
    assert findings[0]["similarity"] >= 0.85


def test_no_duplication_for_different_scenes() -> None:
    scene_a = "The warrior entered the dark cave, sword drawn, ready for battle against the ancient beast."
    scene_b = "Meanwhile at the village market, children played while merchants hawked their wares loudly."
    findings = check_scene_duplication(
        scene_a,
        [(1, 1, scene_b)],
        warning_threshold=0.6,
    )
    assert len(findings) == 0


def test_empty_inputs_no_findings() -> None:
    assert check_scene_duplication("", [(1, 1, "some text")]) == []
    assert check_scene_duplication("some text", []) == []


# ---------------------------------------------------------------------------
# check_opening_diversity
# ---------------------------------------------------------------------------

def test_similar_openings_detected() -> None:
    # Use identical opening text to ensure detection
    opening = "The dark cave shimmered with a sudden burst of golden light that illuminated everything around them"
    existing = [(1, "The dark cave shimmered with a sudden burst of golden light that illuminated everything around them")]
    findings = check_opening_diversity(
        opening,
        existing,
        similarity_threshold=0.7,
    )
    assert len(findings) >= 1
    assert findings[0]["similarity"] >= 0.7


def test_different_openings_no_findings() -> None:
    findings = check_opening_diversity(
        "清晨的阳光洒满了庭院",
        [(1, "午夜的钟声响彻云霄")],
        similarity_threshold=0.7,
    )
    assert len(findings) == 0


def test_empty_opening_no_findings() -> None:
    assert check_opening_diversity("", [(1, "something")]) == []


# ---------------------------------------------------------------------------
# extract_frequent_phrases
# ---------------------------------------------------------------------------

def test_extract_zh_frequent_phrases() -> None:
    # Repeat a phrase many times across texts
    texts = [
        "一股强大的力量涌入体内，一股强大的能量弥漫开来",
        "一股强大的力量笼罩全身，一股强大的气息弥漫开来",
        "一股强大的力量再次涌来，一股强大的气息升腾而起",
        "一股强大的力量席卷而来，一股强大的神识扩散开来",
    ]
    phrases = extract_frequent_phrases(texts, language="zh-CN", min_occurrences=3)
    assert len(phrases) > 0
    # "一股强大的" should appear frequently
    phrase_texts = [p[0] for p in phrases]
    assert any("强大" in p for p in phrase_texts)


def test_extract_en_frequent_phrases() -> None:
    texts = [
        "she took a deep breath and looked at the horizon with a deep breath",
        "he took a deep breath before the battle and took a deep breath after",
        "they all took a deep breath when the storm passed and took a deep breath again",
        "after a deep breath she continued onward with a deep breath of fresh air",
    ]
    phrases = extract_frequent_phrases(texts, language="en", min_occurrences=3)
    assert len(phrases) > 0
    phrase_texts = [p[0] for p in phrases]
    assert any("deep breath" in p for p in phrase_texts)


def test_extract_with_too_few_occurrences() -> None:
    texts = ["unique text one", "unique text two"]
    phrases = extract_frequent_phrases(texts, language="en", min_occurrences=5)
    assert len(phrases) == 0


# ---------------------------------------------------------------------------
# build_overused_phrase_avoidance_block
# ---------------------------------------------------------------------------

def test_avoidance_block_zh() -> None:
    phrases = [("一股强大的力量", 8), ("缓缓说道", 6)]
    block = build_overused_phrase_avoidance_block(phrases, language="zh-CN")
    assert "高频短语避免列表" in block
    assert "一股强大的力量" in block
    assert "8" in block


def test_avoidance_block_en() -> None:
    phrases = [("took a deep breath", 7), ("without a second thought", 5)]
    block = build_overused_phrase_avoidance_block(phrases, language="en")
    assert "OVERUSED PHRASES" in block
    assert "took a deep breath" in block


def test_avoidance_block_empty() -> None:
    assert build_overused_phrase_avoidance_block([], language="zh-CN") == ""


# ---------------------------------------------------------------------------
# detect_intra_chapter_repetition / remove_intra_chapter_duplicates
# ---------------------------------------------------------------------------

_REPEATED_CHAPTER = """\
# 第37章：浮标失衡

焦土边缘，风卷起黑色的灰烬。

宁尘将苏瑶从背上放下。她的后背靠上一块断裂的岩柱，呼吸浅得几乎察觉不到。

"撑住。"他低声说。

这是场景一结尾，新的场景即将开始。

焦土边缘，风卷起黑色的灰烬。

宁尘将苏瑶从背上放下。她的后背靠上一块断裂的岩柱，呼吸浅得几乎察觉不到。
"""

_CLEAN_CHAPTER = """\
# 第36章：铁壁加压

宁尘从焦土中撑起身，左臂传来一阵钝痛。

雷劫的余温还在皮肤上灼烧，但他站住了。

"能站起来吗？"陆沉的声音从侧面传来。

宁尘拍了拍袍角的灰烬："其他人呢？"
"""


def test_detect_intra_chapter_repetition_finds_duplicates() -> None:
    findings = detect_intra_chapter_repetition(_REPEATED_CHAPTER)
    assert len(findings) >= 1
    assert all(f["severity"] == "critical" for f in findings)
    assert all("段落重复" in f["message"] for f in findings)


def test_detect_intra_chapter_repetition_clean_chapter() -> None:
    findings = detect_intra_chapter_repetition(_CLEAN_CHAPTER)
    assert len(findings) == 0


def test_detect_intra_chapter_repetition_empty() -> None:
    assert detect_intra_chapter_repetition("") == []


def test_remove_intra_chapter_duplicates_removes_second_occurrence() -> None:
    cleaned, removed = remove_intra_chapter_duplicates(_REPEATED_CHAPTER)
    # Duplicate paragraphs should be gone
    assert removed >= 1
    # First occurrence should still be present
    assert "焦土边缘，风卷起黑色的灰烬" in cleaned
    # No back-to-back identical occurrences in result
    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    seen: set[str] = set()
    for p in paragraphs:
        assert p not in seen, f"Paragraph still duplicated: {p[:60]}"
        seen.add(p)


def test_remove_intra_chapter_duplicates_clean_chapter_unchanged() -> None:
    cleaned, removed = remove_intra_chapter_duplicates(_CLEAN_CHAPTER)
    assert removed == 0
    # Content should be functionally identical (only whitespace may differ)
    assert "宁尘从焦土中撑起身" in cleaned
    assert "雷劫的余温还在皮肤上灼烧" in cleaned


def test_remove_intra_chapter_duplicates_keeps_ultra_short_lines() -> None:
    # Ultra-short paragraphs (< 12 chars) should not be deduplicated.
    # "快。" is 2 chars — too short to be a meaningful dedup candidate.
    text = "# 标题\n\n快。\n\n正文内容，足够长的段落用来触发去重逻辑，不少于十二个字。\n\n快。\n\n正文内容，足够长的段落用来触发去重逻辑，不少于十二个字。"
    cleaned, removed = remove_intra_chapter_duplicates(text)
    assert removed == 1  # Only the long paragraph should be removed
    assert cleaned.count("快。") == 2  # Ultra-short lines preserved


# ---------------------------------------------------------------------------
# clean_meta_text_markers
# ---------------------------------------------------------------------------

def test_clean_meta_text_removes_bold_chapter_end() -> None:
    text = "场景结尾的正文内容，角色说了最后一句话。\n\n**第28章 完**\n"
    cleaned, removed = clean_meta_text_markers(text)
    assert removed == 1
    assert "第28章 完" not in cleaned
    assert "场景结尾的正文内容" in cleaned


def test_clean_meta_text_removes_parenthetical_end() -> None:
    text = "宁尘深吸一口气。\n\n（本章完）\n"
    cleaned, removed = clean_meta_text_markers(text)
    assert removed == 1
    assert "本章完" not in cleaned


def test_clean_meta_text_clean_text_unchanged() -> None:
    text = "这是干净的正文，没有任何元数据标记。宁尘抬起头，看向远处的峰峦。\n"
    cleaned, removed = clean_meta_text_markers(text)
    assert removed == 0
    assert cleaned == text


# ---------------------------------------------------------------------------
# check_hook_repetition
# ---------------------------------------------------------------------------

def test_hook_repetition_detected() -> None:
    hook = "而真正的风暴，才刚刚开始。"
    existing = [(15, "而真正的风暴，才刚刚开始。")]
    findings = check_hook_repetition(hook, existing, similarity_threshold=0.75)
    assert len(findings) >= 1
    assert findings[0]["chapter"] == 15


def test_hook_repetition_different_hooks_no_findings() -> None:
    hook = "石壁轰然崩塌。宁尘坠入黑暗。"
    existing = [(15, "远处传来了三声悠长的钟鸣，宣告着这个夜晚的结束。")]
    findings = check_hook_repetition(hook, existing, similarity_threshold=0.75)
    assert len(findings) == 0


def test_hook_repetition_empty_inputs() -> None:
    assert check_hook_repetition("", [(1, "some hook")]) == []
    assert check_hook_repetition("some hook", []) == []


# ---------------------------------------------------------------------------
# build_opening_diversity_block
# ---------------------------------------------------------------------------

def test_opening_diversity_block_zh() -> None:
    openings = [(14, "清晨的雾气还没散尽，宁尘站在演武场外。"), (15, "清晨的薄雾还未散尽，演武场四周已经挤满。")]
    block = build_opening_diversity_block(openings, language="zh-CN")
    assert "最近章节开头" in block
    assert "第14章" in block
    assert "第15章" in block
    assert "开场不得重复" in block


def test_opening_diversity_block_en() -> None:
    openings = [(1, "The amber light flickered in the tunnel."), (2, "The corridor stretched before him.")]
    block = build_opening_diversity_block(openings, language="en")
    assert "RECENT CHAPTER OPENINGS" in block
    assert "Ch1" in block
    assert "Ch2" in block
    assert "The" in block


def test_opening_diversity_block_empty() -> None:
    assert build_opening_diversity_block([], language="zh-CN") == ""


# ---------------------------------------------------------------------------
# Paraphrase-aware intra-chapter dedup (Layer 0 root fix)
# ---------------------------------------------------------------------------

_PARAPHRASED_CHAPTER = """\
# 第170章：遗跡深处

宁尘迈步向前，踏入幽暗的石廊之中。空气潮湿而厚重，仿佛被时间压榨过无数次。

他伸手抚上石壁，粗糙的纹路在指尖下流淌，宛如古老的鳞片。脚下的回音一阵一阵地弹回耳膜。

这是一段不会被认为重复的文字，内容与其他段落完全不同，描写的是完全不同的场景细节。

宁尘迈步向前，踏入幽暗的石廊之中。空气潮湿而厚重，仿佛被无数时间压榨过。

他伸手抚上石壁，粗糙的纹路在指尖下流淌，宛如古老的鳞片。脚下的回音一阵一阵地弹着回耳膜。
"""


def test_detect_intra_chapter_repetition_paraphrase() -> None:
    """Paraphrased paragraphs should be caught by the shingle-based detector."""
    findings = detect_intra_chapter_repetition(
        _PARAPHRASED_CHAPTER,
        paraphrase_threshold=0.45,
    )
    # Expect at least one paraphrase finding (the 2 near-identical paragraphs)
    assert len(findings) >= 1
    # Paraphrased duplicates are less severe than byte-exact ones; accept either major or critical
    assert all(f.get("severity") in {"major", "critical"} for f in findings)


def test_remove_intra_chapter_duplicates_paraphrase_removes_rewrites() -> None:
    """Paraphrased duplicate paragraphs should be removed by the paraphrase-aware cleaner."""
    cleaned, removed = remove_intra_chapter_duplicates_paraphrase(
        _PARAPHRASED_CHAPTER,
        paraphrase_threshold=0.45,
    )
    assert removed >= 1
    # The non-duplicate sentinel sentence should survive
    assert "内容与其他段落完全不同" in cleaned
    # Header should survive
    assert "# 第170章" in cleaned


def test_remove_intra_chapter_duplicates_paraphrase_clean_chapter() -> None:
    """A clean chapter should pass through untouched (removed == 0)."""
    cleaned, removed = remove_intra_chapter_duplicates_paraphrase(_CLEAN_CHAPTER)
    assert removed == 0
    assert cleaned.strip() == _CLEAN_CHAPTER.strip()


def test_remove_intra_chapter_duplicates_paraphrase_empty() -> None:
    cleaned, removed = remove_intra_chapter_duplicates_paraphrase("")
    assert cleaned == ""
    assert removed == 0


def test_detect_intra_chapter_repetition_accepts_threshold_kwarg() -> None:
    """Regression guard: reviewer passes paraphrase_threshold explicitly."""
    # Should not raise; equivalent behavior with or without the kwarg on byte-exact duplicates
    findings_with_kwarg = detect_intra_chapter_repetition(
        _REPEATED_CHAPTER, paraphrase_threshold=0.55
    )
    findings_default = detect_intra_chapter_repetition(_REPEATED_CHAPTER)
    assert len(findings_with_kwarg) == len(findings_default)
