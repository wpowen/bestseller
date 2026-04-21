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
    detect_chapter_text_loop,
    detect_intra_chapter_repetition,
    detect_short_cluster_near_repeat,
    extract_frequent_phrases,
    remove_chapter_text_loops,
    remove_intra_chapter_duplicates,
    remove_intra_chapter_duplicates_paraphrase,
    remove_short_cluster_near_repeats,
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


# ---------------------------------------------------------------------------
# Block-loop detector (LLM stuck-in-loop failure mode)
# ---------------------------------------------------------------------------

# Mirrors the actual chapter-181 failure: 17-paragraph block of short lines
# repeats 5x back-to-back. Each line is under the _MIN_PARA_LEN (12-char)
# threshold so per-paragraph dedup misses them entirely.
_LOOP_BLOCK = "\n\n".join([
    "“我选第三个。”",
    "宁尘抬起手。",
    "“你——”",
    "“砰！”",
    "殿主的眉头皱起。",
    "他忽然明白了什么。",
    "宁尘没有说话。",
    "然后他看见了陆沉。",
    "“你来了。”",
    "“我看见了。”",
])


def test_detect_chapter_text_loop_finds_consecutive_repeat() -> None:
    # Block repeats 3 times back-to-back — per-paragraph dedup wouldn't catch
    # any of these because each line is < 12 chars.
    text = "\n\n".join([_LOOP_BLOCK, _LOOP_BLOCK, _LOOP_BLOCK])
    loops = detect_chapter_text_loop(text)
    assert len(loops) == 1
    assert loops[0]["window_size"] == 10
    assert loops[0]["repeats"] == 3
    assert loops[0]["severity"] == "critical"


def test_detect_chapter_text_loop_needs_min_repeats() -> None:
    # A single occurrence is not a loop.
    loops = detect_chapter_text_loop(_LOOP_BLOCK)
    assert loops == []


def test_detect_chapter_text_loop_ignores_clean_chapter() -> None:
    clean = (
        "宁尘走进偏殿，空气中弥漫着霉味。\n\n"
        "他蹲下身，按住石板。\n\n"
        "青白色的光芒与符文共振。\n\n"
        "甬道在他面前缓缓展开。\n\n"
        "尽头的金光越来越盛。"
    )
    assert detect_chapter_text_loop(clean) == []


def test_remove_chapter_text_loops_keeps_first_copy_only() -> None:
    text = "\n\n".join([_LOOP_BLOCK, _LOOP_BLOCK, _LOOP_BLOCK, _LOOP_BLOCK])
    cleaned, removed = remove_chapter_text_loops(text)
    # 10 paragraphs per block × (4 - 1) dropped copies = 30 paragraphs removed
    assert removed == 30
    # Cleaned text contains the block exactly once.
    assert cleaned.count("我选第三个") == 1
    # Structure preserved.
    assert "砰！" in cleaned


def test_remove_chapter_text_loops_is_idempotent() -> None:
    text = "\n\n".join([_LOOP_BLOCK, _LOOP_BLOCK])
    cleaned_once, r1 = remove_chapter_text_loops(text)
    cleaned_twice, r2 = remove_chapter_text_loops(cleaned_once)
    assert r2 == 0
    assert cleaned_once == cleaned_twice


def test_remove_chapter_text_loops_preserves_non_loop_content() -> None:
    prefix = "宁尘的脚尖刚踏过废墟的最后一块碎石。\n\n他的视线扫过四周。"
    suffix = "甬道中一片死寂。\n\n他的呼吸声在墙壁间回荡。"
    text = "\n\n".join([prefix, _LOOP_BLOCK, _LOOP_BLOCK, _LOOP_BLOCK, suffix])
    cleaned, removed = remove_chapter_text_loops(text)
    assert removed == 20  # 2 dropped copies × 10 paragraphs
    assert "脚尖刚踏过废墟" in cleaned
    assert "甬道中一片死寂" in cleaned


def test_detect_chapter_text_loop_catches_minimum_window() -> None:
    # 3 paragraphs repeated 2x — smallest loop we accept.
    block = "“砰！”\n\n他抬起手。\n\n“你——”"
    text = "\n\n".join([block, block])
    loops = detect_chapter_text_loop(text)
    assert len(loops) == 1
    assert loops[0]["window_size"] == 3
    assert loops[0]["repeats"] == 2


def test_detect_chapter_text_loop_prefers_larger_window() -> None:
    # A 6-paragraph block that repeats 2x. A naive detector might report this
    # as a window=3 / repeats=4 if the inner 3-para sub-window happens to
    # match too — we prefer reporting the *outer* loop window=6.
    inner = "他抬起手。\n\n“砰！”\n\n他放下手。"
    block = "\n\n".join([inner, "殿主的眉头皱起。", "他忽然明白了什么。", "宁尘没有说话。"])
    text = "\n\n".join([block, block])
    loops = detect_chapter_text_loop(text)
    assert len(loops) == 1
    # Window should be the outer 6 (not the inner 3).
    assert loops[0]["window_size"] == 6
    assert loops[0]["repeats"] == 2


def test_chapter_181_style_loop_is_caught() -> None:
    """End-to-end: the exact pattern from 道种破虚 ch181 gets collapsed."""
    # 17-paragraph short-line block, repeated 5 times — matches ch181 scenario.
    ch181_block = "\n\n".join([
        "“我选第三个。”",
        "宁尘抬起手。",
        "青白色的光芒从他掌心炸开。",
        "“你——”",
        "殿主后退一步，眼中闪过一丝意外。",
        "“砰！”",
        "殿主的眉头皱起。",
        "他忽然明白了什么。",
        "宁尘没有说话。",
        "然后他看见了陆沉。",
        "“你来了。”",
        "“我看见了。”",
        "他的目光落在宁尘身后。",
        "“苏瑶。”",
        "脚步声停了。",
        "掌心的烙印猛然一跳。",
        "殿主的眼神变了。",
    ])
    text = "\n\n".join([ch181_block] * 5)
    loops = detect_chapter_text_loop(text)
    assert loops, "detector must catch the ch181 loop pattern"
    assert loops[0]["repeats"] == 5
    cleaned, removed = remove_chapter_text_loops(text)
    assert removed == 17 * 4  # 4 dropped copies × 17 paragraphs each
    # First copy retained
    assert cleaned.count("我选第三个") == 1


# ---------------------------------------------------------------------------
# Fuzzy short-line cluster near-repeat detector
# ---------------------------------------------------------------------------

def test_detect_short_cluster_near_repeat_catches_nonidentical_echo() -> None:
    # Two adjacent clusters of short lines with ~85% hash overlap but
    # different lengths — the exact-match block detector cannot catch this.
    cluster_a = "\n\n".join([
        "“我选第三个。”",
        "宁尘抬起手。",
        "“你——”",
        "殿主后退一步，眼中闪过一丝意外。",  # long line inside cluster
        "“砰！”",
        "殿主的眉头皱起。",
        "他忽然明白了什么。",
        "宁尘没有说话。",
        "然后他看见了陆沉。",
        "“你来了。”",
        "“我看见了。”",
        "他的目光落在宁尘身后。",
        "“苏瑶。”",
        "脚步声停了。",
        "可她的眼睛是清醒的。",
        "掌心的烙印猛然一跳。",
        "殿主的眼神变了。",
    ])
    cluster_b = "\n\n".join([
        "“我选第三个。”",
        "宁尘抬起手。",
        "“你——”",
        "“砰！”",
        "殿主的眉头皱起。",
        "他忽然明白了什么。",
        "宁尘没有说话。",
        "然后他看见了陆沉。",
        "“你来了。”",
        "“我看见了。”",
        "他的目光落在宁尘身后。",
        "“苏瑶。”",
        "脚步声停了。",
    ])
    text = cluster_a + "\n\n" + cluster_b
    findings = detect_short_cluster_near_repeat(text)
    assert findings, "must detect the non-identical short-line echo"
    # cluster_b has 13 short paragraphs, all echoing cluster_a
    assert len(findings) >= 12


def test_remove_short_cluster_near_repeats_keeps_first_occurrence() -> None:
    cluster = "\n\n".join([
        "“我选第三个。”",
        "宁尘抬起手。",
        "“你——”",
        "“砰！”",
        "殿主的眉头皱起。",
        "他忽然明白了什么。",
        "宁尘没有说话。",
        "然后他看见了陆沉。",
        "“你来了。”",
        "“我看见了。”",
    ])
    text = cluster + "\n\n" + cluster
    cleaned, removed = remove_short_cluster_near_repeats(text)
    assert removed == 10, "second cluster should be entirely dropped"
    assert cleaned.count("我选第三个") == 1


def test_short_cluster_near_repeat_ignores_narrative_region() -> None:
    # Short lines scattered in a narrative-rich (long-paragraph-heavy) context
    # should NOT be flagged — they are not in a short-line-dense region.
    narrative = "\n\n".join([
        "宁尘盯着陆沉的背影，三百年的时光在那具身躯上刻下了太深的痕迹。",
        "“你来了。”",  # short line repeated below but not in dense context
        "甬道里的空气像被抽干了，远处传来水滴落在石砖上的细微声响。",
        "苏瑶慢慢走近，黑色的气息在她周身流转，像是活物。",
        "“你来了。”",
        "殿主抬起手，金光在他掌心凝聚，空气都被扭曲得变了形。",
    ])
    findings = detect_short_cluster_near_repeat(narrative)
    assert findings == [], "short lines in narrative region must not be flagged"


def test_short_cluster_near_repeat_skips_ultrashort_fragments() -> None:
    # 1-2 char fragments ("好。" / "嗯。") inside a short-dense region should
    # not be treated as meaningful — they repeat legitimately in dialogue.
    cluster = "\n\n".join([
        "“好。”",
        "“嗯。”",
        "“快。”",
        "“走。”",
        "“好。”",
        "“嗯。”",
    ])
    findings = detect_short_cluster_near_repeat(cluster, min_short_line_len=3)
    assert findings == []


def test_short_cluster_near_repeat_is_idempotent() -> None:
    cluster = "\n\n".join([
        "“我选第三个。”",
        "宁尘抬起手。",
        "“你——”",
        "“砰！”",
        "殿主的眉头皱起。",
        "他忽然明白了什么。",
        "宁尘没有说话。",
    ])
    text = cluster + "\n\n" + cluster + "\n\n" + cluster
    cleaned_once, r1 = remove_short_cluster_near_repeats(text)
    cleaned_twice, r2 = remove_short_cluster_near_repeats(cleaned_once)
    assert r1 > 0
    assert r2 == 0
    assert cleaned_twice == cleaned_once
