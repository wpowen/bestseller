"""Cross-chapter content deduplication and repetitive pattern detection.

Provides three layers of deduplication:

1. **Scene fingerprinting** — detect near-verbatim copies between scenes using
   n-gram shingling + Jaccard similarity.
2. **Opening diversity** — prevent chapters from starting with the same text.
3. **Phrase frequency tracking** — detect book-level overused phrases and inject
   an avoidance list into the writer prompt.

All checks are local computation — zero LLM cost.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Scene fingerprinting via n-gram shingling
# ---------------------------------------------------------------------------

_SHINGLE_SIZE = 5  # words per shingle


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, collapse whitespace."""
    text = re.sub(r"\s+", " ", text.strip().lower())
    # Remove common markdown artifacts
    text = re.sub(r"[#*_`>\-=]", "", text)
    return text


def _compute_shingle_set(text: str, shingle_size: int = _SHINGLE_SIZE) -> set[int]:
    """Compute a set of hashed word-level shingles from text."""
    normalized = _normalize_text(text)
    if not normalized:
        return set()
    words = normalized.split()
    if len(words) < shingle_size:
        return {hash(normalized)}
    shingles: set[int] = set()
    for i in range(len(words) - shingle_size + 1):
        shingle = " ".join(words[i : i + shingle_size])
        shingles.add(int(hashlib.md5(shingle.encode()).hexdigest()[:8], 16))
    return shingles


def compute_jaccard_similarity(text_a: str, text_b: str) -> float:
    """Compute Jaccard similarity between two texts using shingle sets."""
    set_a = _compute_shingle_set(text_a)
    set_b = _compute_shingle_set(text_b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def check_scene_duplication(
    new_scene_text: str,
    existing_scene_texts: list[tuple[int, int, str]],
    *,
    warning_threshold: float = 0.6,
    critical_threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """Check if new scene text is too similar to any existing scene.

    Parameters
    ----------
    new_scene_text : str
        The newly generated scene text.
    existing_scene_texts : list of (chapter_number, scene_number, text)
        Previously generated scene texts.
    warning_threshold : float
        Jaccard similarity above this triggers a warning.
    critical_threshold : float
        Jaccard similarity above this triggers a critical finding.

    Returns
    -------
    list of findings dicts with keys: chapter, scene, similarity, severity, message
    """
    if not new_scene_text or not existing_scene_texts:
        return []

    findings: list[dict[str, Any]] = []
    new_shingles = _compute_shingle_set(new_scene_text)
    if not new_shingles:
        return []

    for ch_num, sc_num, existing_text in existing_scene_texts:
        if not existing_text:
            continue
        existing_shingles = _compute_shingle_set(existing_text)
        if not existing_shingles:
            continue

        intersection = len(new_shingles & existing_shingles)
        union = len(new_shingles | existing_shingles)
        similarity = intersection / union if union > 0 else 0.0

        if similarity >= critical_threshold:
            findings.append({
                "chapter": ch_num,
                "scene": sc_num,
                "similarity": round(similarity, 3),
                "severity": "critical",
                "message": (
                    f"[内容重复] 与第{ch_num}章第{sc_num}场的相似度为 {similarity:.1%}，"
                    f"疑似逐字复制。必须重写以避免重复内容。"
                ),
            })
        elif similarity >= warning_threshold:
            findings.append({
                "chapter": ch_num,
                "scene": sc_num,
                "similarity": round(similarity, 3),
                "severity": "major",
                "message": (
                    f"[内容相似] 与第{ch_num}章第{sc_num}场的相似��为 {similarity:.1%}，"
                    f"建议调整以增加差异性。"
                ),
            })

    return findings


# ---------------------------------------------------------------------------
# 2. Chapter opening diversity
# ---------------------------------------------------------------------------

def check_opening_diversity(
    new_opening: str,
    existing_openings: list[tuple[int, str]],
    *,
    similarity_threshold: float = 0.7,
    opening_length: int = 100,
) -> list[dict[str, Any]]:
    """Check if a chapter opening is too similar to previous chapter openings.

    Parameters
    ----------
    new_opening : str
        First ``opening_length`` characters of the new chapter.
    existing_openings : list of (chapter_number, opening_text)
        Previous chapter openings.
    similarity_threshold : float
        Jaccard similarity above this triggers a finding.
    opening_length : int
        How many characters to compare.

    Returns
    -------
    list of finding dicts
    """
    if not new_opening:
        return []

    new_text = _normalize_text(new_opening[:opening_length])
    findings: list[dict[str, Any]] = []

    for ch_num, existing_opening in existing_openings:
        if not existing_opening:
            continue
        existing_text = _normalize_text(existing_opening[:opening_length])
        similarity = compute_jaccard_similarity(new_text, existing_text)
        if similarity >= similarity_threshold:
            findings.append({
                "chapter": ch_num,
                "similarity": round(similarity, 3),
                "severity": "major",
                "message": (
                    f"[开头重复] 本章开头与第{ch_num}章开头相似度为 {similarity:.1%}，"
                    f"请改写开头以增加多样性。"
                ),
            })

    return findings


# ---------------------------------------------------------------------------
# 3. Book-level phrase frequency tracking
# ---------------------------------------------------------------------------

# Minimum phrase length (in characters for CJK, words for Latin)
_MIN_PHRASE_LEN_CJK = 4
_MIN_PHRASE_LEN_WORDS = 3
_MAX_PHRASE_LEN_CJK = 12
_MAX_PHRASE_LEN_WORDS = 8

# Common stop phrases to ignore
_STOP_PHRASES_ZH = frozenset({
    "的时候", "一个人", "这个时候", "的样子", "可以说",
    "不知道", "的话", "然后", "因为", "所以",
})

_STOP_PHRASES_EN = frozenset({
    "in the", "of the", "on the", "at the", "to the",
    "it was", "he was", "she was", "they were", "there was",
})


def extract_frequent_phrases(
    texts: list[str],
    *,
    language: str = "zh-CN",
    min_occurrences: int = 4,
    max_phrases: int = 20,
) -> list[tuple[str, int]]:
    """Extract frequently repeated phrases across multiple text segments.

    Returns a list of (phrase, count) sorted by count descending.
    """
    is_zh = language.lower().startswith("zh")
    counter: Counter[str] = Counter()

    for text in texts:
        if not text:
            continue
        normalized = _normalize_text(text)

        if is_zh:
            _extract_zh_phrases(normalized, counter)
        else:
            _extract_en_phrases(normalized, counter)

    # Filter and sort
    stop = _STOP_PHRASES_ZH if is_zh else _STOP_PHRASES_EN
    results = [
        (phrase, count)
        for phrase, count in counter.most_common(max_phrases * 3)
        if count >= min_occurrences and phrase not in stop
    ]
    return results[:max_phrases]


def _extract_zh_phrases(text: str, counter: Counter[str]) -> None:
    """Extract CJK character n-grams as phrase candidates."""
    # Extract runs of CJK characters
    cjk_runs = re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]{4,20}", text)
    for run in cjk_runs:
        for length in range(_MIN_PHRASE_LEN_CJK, min(len(run) + 1, _MAX_PHRASE_LEN_CJK + 1)):
            for start in range(len(run) - length + 1):
                phrase = run[start : start + length]
                counter[phrase] += 1


def _extract_en_phrases(text: str, counter: Counter[str]) -> None:
    """Extract word n-grams as phrase candidates."""
    words = text.split()
    for length in range(_MIN_PHRASE_LEN_WORDS, min(len(words) + 1, _MAX_PHRASE_LEN_WORDS + 1)):
        for start in range(len(words) - length + 1):
            phrase = " ".join(words[start : start + length])
            counter[phrase] += 1


def build_overused_phrase_avoidance_block(
    phrases: list[tuple[str, int]],
    *,
    language: str = "zh-CN",
) -> str:
    """Render a prompt block listing overused phrases to avoid."""
    if not phrases:
        return ""

    is_zh = language.lower().startswith("zh")

    if is_zh:
        lines = ["【高频短语避免列表 — 请使用替代表达】"]
        for phrase, count in phrases[:15]:
            lines.append(f"• 「{phrase}」(已出现{count}次) — 请换用不同表达")
    else:
        lines = ["[OVERUSED PHRASES — use alternative expressions]"]
        for phrase, count in phrases[:15]:
            lines.append(f"• \"{phrase}\" ({count} occurrences) — vary your phrasing")

    return "\n".join(lines)
