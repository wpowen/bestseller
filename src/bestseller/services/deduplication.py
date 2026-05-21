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
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Scene fingerprinting via n-gram shingling
# ---------------------------------------------------------------------------

_SHINGLE_SIZE = 5        # words per shingle (English)
_CJK_SHINGLE_SIZE = 4   # characters per shingle (Chinese/CJK)

# Regex that matches any CJK Unified Ideograph
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, collapse whitespace."""
    text = re.sub(r"\s+", " ", text.strip().lower())
    # Remove common markdown artifacts
    text = re.sub(r"[#*_`>\-=]", "", text)
    return text


def _is_cjk_dominant(text: str) -> bool:
    """Return True when more than 30% of non-space characters are CJK."""
    stripped = text.replace(" ", "")
    if not stripped:
        return False
    return len(_CJK_RE.findall(stripped)) / len(stripped) > 0.3


def _compute_shingle_set(text: str, shingle_size: int = _SHINGLE_SIZE) -> set[int]:
    """Compute a set of hashed n-gram shingles from text.

    For CJK-dominant text (Chinese) character-level n-grams are used because
    Chinese has no whitespace word boundaries.  For Latin/English text the
    original word-level shingles are used.
    """
    normalized = _normalize_text(text)
    if not normalized:
        return set()

    if _is_cjk_dominant(normalized):
        # Character-level shingles: strip spaces so punctuation runs together
        chars = normalized.replace(" ", "")
        k = _CJK_SHINGLE_SIZE
        if len(chars) < k:
            return {int(hashlib.md5(chars.encode()).hexdigest()[:8], 16)}
        shingles: set[int] = set()
        for i in range(len(chars) - k + 1):
            shingle = chars[i : i + k]
            shingles.add(int(hashlib.md5(shingle.encode()).hexdigest()[:8], 16))
        return shingles

    # English / Latin: word-level shingles (original behaviour)
    words = normalized.split()
    if len(words) < shingle_size:
        return {int(hashlib.md5(normalized.encode()).hexdigest()[:8], 16)}
    shingles = set()
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


_CROSS_CHAPTER_PUNCT_RE = re.compile(r"[\s#*_`>\-=，。！？、；：“”‘’（）()【】\[\]《》,.!?;:'\"…·]+")


def _normalize_cross_chapter_paragraph(text: str) -> str:
    """Normalize a paragraph key for publication-grade cross-chapter checks."""
    return _CROSS_CHAPTER_PUNCT_RE.sub("", _normalize_text(text))


def detect_cross_chapter_repetition(
    chapter_texts: list[tuple[int, str]],
    *,
    min_paragraph_length: int = 20,
    chapter_similarity_threshold: float = 0.35,
    chapter_similarity_window: int = 5,
    max_findings: int = 50,
) -> list[dict[str, Any]]:
    """Detect repeated publishable material across chapters.

    This is a hard publication safety net. Generation-time scene checks may
    warn or auto-clean, but platform submission needs a deterministic final
    check for copied paragraphs or near-duplicate adjacent chapters.
    """
    ordered = [(int(chapter), text or "") for chapter, text in chapter_texts if text]
    ordered.sort(key=lambda item: item[0])
    findings: list[dict[str, Any]] = []

    seen_paragraphs: dict[str, tuple[int, int, str]] = {}
    reported_paragraph_pairs: set[tuple[int, int, int, int]] = set()
    for chapter_number, text in ordered:
        for para_index, paragraph in enumerate(_split_paragraphs(text), start=1):
            key = _normalize_cross_chapter_paragraph(paragraph)
            if len(key) < min_paragraph_length:
                continue
            prior = seen_paragraphs.get(key)
            if prior is None:
                seen_paragraphs[key] = (chapter_number, para_index, paragraph)
                continue
            prior_chapter, prior_index, prior_text = prior
            if prior_chapter == chapter_number:
                continue
            pair_key = (prior_chapter, prior_index, chapter_number, para_index)
            if pair_key in reported_paragraph_pairs:
                continue
            reported_paragraph_pairs.add(pair_key)
            sample = paragraph[:120].replace("\n", " ")
            findings.append({
                "chapter": chapter_number,
                "source_chapter": prior_chapter,
                "paragraph": para_index,
                "source_paragraph": prior_index,
                "similarity": 1.0,
                "severity": "critical",
                "text": sample,
                "source_text": prior_text[:120].replace("\n", " "),
                "message": (
                    f"[跨章段落重复] 第{chapter_number}章第{para_index}段与"
                    f"第{prior_chapter}章第{prior_index}段重复：{sample}。"
                    f"发布前必须重写。"
                ),
            })
            if len(findings) >= max_findings:
                return findings

    shingle_sets = [
        (chapter_number, _compute_shingle_set(text))
        for chapter_number, text in ordered
    ]
    for idx, (chapter_a, shingles_a) in enumerate(shingle_sets):
        if not shingles_a:
            continue
        for chapter_b, shingles_b in shingle_sets[idx + 1 :]:
            if chapter_b - chapter_a > chapter_similarity_window:
                break
            if not shingles_b:
                continue
            union = len(shingles_a | shingles_b)
            if union == 0:
                continue
            similarity = len(shingles_a & shingles_b) / union
            if similarity < chapter_similarity_threshold:
                continue
            findings.append({
                "chapter": chapter_b,
                "source_chapter": chapter_a,
                "similarity": round(similarity, 3),
                "severity": "critical",
                "message": (
                    f"[跨章整体重复] 第{chapter_b}章与第{chapter_a}章整体相似度"
                    f" {similarity:.1%}，疑似重复章节。发布前必须重写。"
                ),
            })
            if len(findings) >= max_findings:
                return findings

    return findings


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


# ---------------------------------------------------------------------------
# 4. Intra-chapter paragraph-level deduplication
# ---------------------------------------------------------------------------
# Minimum paragraph length (chars) to consider as a duplication candidate.
# 12 chars handles short but meaningful CJK lines like "焦土边缘，风卷起黑色的灰烬。" (16 chars)
# while excluding ultra-short isolated lines like "快。" (2 chars) or "他说。" (3 chars).
_MIN_PARA_LEN = 12


def _split_paragraphs(text: str) -> list[str]:
    """Split text into non-empty paragraphs."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def detect_intra_chapter_repetition(
    chapter_text: str,
    *,
    min_paragraph_length: int = _MIN_PARA_LEN,
    paraphrase_threshold: float = 0.55,
) -> list[dict[str, Any]]:
    """Detect repeated paragraph blocks within a single assembled chapter.

    Detects TWO kinds of duplication:

    1. **Byte-exact** (MD5 of normalized text) — catches copy-paste duplicates.
    2. **Paraphrased** (Jaccard shingle similarity) — catches the same
       dialog/action rewritten with minor variation, e.g. punctuation or
       phrasing changes. This is the kind produced by chapter-rewrite LLMs
       that "echo" upstream scenes in slightly altered form.

    Parameters
    ----------
    chapter_text : str
        The fully assembled chapter markdown content.
    min_paragraph_length : int
        Minimum character length for a paragraph to be considered a duplicate
        candidate. Short paragraphs (single-sentence transitions) are ignored.
    paraphrase_threshold : float
        Jaccard similarity above this value flags a paragraph pair as a
        paraphrased duplicate (default 0.55).

    Returns
    -------
    list of findings dicts with keys:
        first_pos  — index of first occurrence paragraph
        second_pos — index of duplicate paragraph
        text       — the repeated paragraph text (truncated to 120 chars)
        similarity — Jaccard similarity score (1.0 for byte-exact)
        severity   — "critical" (byte-exact) or "major" (paraphrased)
        message    — human-readable description
    """
    paragraphs = _split_paragraphs(chapter_text)
    findings: list[dict[str, Any]] = []
    seen: dict[int, int] = {}          # normalized_hash → first paragraph index
    flagged_as_duplicate: set[int] = set()  # paragraphs already flagged (don't double-count)
    shingle_cache: dict[int, set[int]] = {}

    for i, para in enumerate(paragraphs):
        normalized = _normalize_text(para)
        if len(normalized) < min_paragraph_length:
            continue
        key = int(hashlib.md5(normalized.encode()).hexdigest()[:12], 16)
        if key in seen:
            findings.append({
                "first_pos": seen[key],
                "second_pos": i,
                "text": para[:120],
                "similarity": 1.0,
                "severity": "critical",
                "message": (
                    f"[段落重复] 第{i+1}段与第{seen[key]+1}段内容完全相同，"
                    f"疑似场景拼接时产生重复。"
                ),
            })
            flagged_as_duplicate.add(i)
            continue
        seen[key] = i
        shingle_cache[i] = _compute_shingle_set(para)

    # Paraphrase detection — pairwise Jaccard on cached shingle sets
    indices = sorted(shingle_cache.keys())
    for idx_a, a in enumerate(indices):
        if a in flagged_as_duplicate:
            continue
        sa = shingle_cache[a]
        if not sa:
            continue
        for b in indices[idx_a + 1 :]:
            if b in flagged_as_duplicate:
                continue
            sb = shingle_cache[b]
            if not sb:
                continue
            u = len(sa | sb)
            if u == 0:
                continue
            sim = len(sa & sb) / u
            if sim >= paraphrase_threshold:
                findings.append({
                    "first_pos": a,
                    "second_pos": b,
                    "text": paragraphs[b][:120],
                    "similarity": round(sim, 3),
                    "severity": "major",
                    "message": (
                        f"[段落改写重复] 第{b+1}段与第{a+1}段相似度 {sim:.1%}，"
                        f"疑似同内容的改写复制。"
                    ),
                })
                flagged_as_duplicate.add(b)

    return findings


def remove_intra_chapter_duplicates_paraphrase(
    chapter_text: str,
    *,
    paraphrase_threshold: float = 0.55,
    min_paragraph_length: int = _MIN_PARA_LEN,
) -> tuple[str, int]:
    """Remove byte-exact AND paraphrased duplicate paragraphs.

    Keeps the first occurrence of each unique paragraph; discards later
    paragraphs that are either byte-identical OR have Jaccard shingle
    similarity ≥ ``paraphrase_threshold`` with any earlier paragraph.

    Returns
    -------
    (cleaned_text, removed_count)
    """
    paragraphs = _split_paragraphs(chapter_text)
    kept: list[str] = []
    kept_shingles: list[set[int]] = []
    seen_exact: set[int] = set()
    removed = 0

    for para in paragraphs:
        normalized = _normalize_text(para)
        if len(normalized) < min_paragraph_length:
            kept.append(para)
            continue
        exact_key = int(hashlib.md5(normalized.encode()).hexdigest()[:12], 16)
        if exact_key in seen_exact:
            removed += 1
            logger.warning(
                "Removing exact-duplicate paragraph (len=%d): %s…",
                len(para), para[:60].replace("\n", " "),
            )
            continue
        # Paraphrase check — against all previously kept long paragraphs
        candidate_shingles = _compute_shingle_set(para)
        is_paraphrase_dup = False
        if candidate_shingles:
            for prior_shingles in kept_shingles:
                u = len(candidate_shingles | prior_shingles)
                if u == 0:
                    continue
                sim = len(candidate_shingles & prior_shingles) / u
                if sim >= paraphrase_threshold:
                    is_paraphrase_dup = True
                    logger.warning(
                        "Removing paraphrased-duplicate paragraph (sim=%.2f, len=%d): %s…",
                        sim, len(para), para[:60].replace("\n", " "),
                    )
                    break
        if is_paraphrase_dup:
            removed += 1
            continue
        seen_exact.add(exact_key)
        if candidate_shingles:
            kept_shingles.append(candidate_shingles)
        kept.append(para)

    return "\n\n".join(kept), removed


# ---------------------------------------------------------------------------
# 4b. Block-level LLM-loop detector
# ---------------------------------------------------------------------------
# When an LLM enters a pathological looping state (for example chapter 181 of
# 道种破虚 which repeats a 17-paragraph block 5× in a row), each *individual*
# paragraph may be well under the 12-char threshold used by the per-paragraph
# dedup above, so the paraphrase detector is blind to it. This function walks
# the paragraph stream looking for *sequences* of paragraphs that repeat
# consecutively — treating the block, not the line, as the comparison unit.

_LOOP_MIN_WINDOW = 3       # smallest block (paragraphs) to consider a loop
_LOOP_MAX_WINDOW = 30      # largest block to search — bigger windows are rare
_LOOP_MIN_REPEATS = 2      # need ≥2 consecutive repeats of the same block


def detect_chapter_text_loop(
    chapter_text: str,
    *,
    min_window: int = _LOOP_MIN_WINDOW,
    max_window: int = _LOOP_MAX_WINDOW,
    min_repeats: int = _LOOP_MIN_REPEATS,
) -> list[dict[str, Any]]:
    """Detect consecutive repeating paragraph blocks (LLM looping failure mode).

    Returns a list of dicts describing each loop. Unlike per-paragraph dedup,
    the minimum length check applies to the **block** (window × repeats),
    not to individual paragraphs — so short-line loops ("砰！" / "他抬起手。"
    / ...) are caught.
    """
    paragraphs = _split_paragraphs(chapter_text)
    if len(paragraphs) < min_window * min_repeats:
        return []
    hashes = [
        int(hashlib.md5(_normalize_text(p).encode()).hexdigest()[:12], 16)
        for p in paragraphs
    ]
    findings: list[dict[str, Any]] = []
    n = len(hashes)
    i = 0
    while i < n:
        best_match: tuple[int, int] | None = None
        # Prefer the smallest matching window — that is the fundamental period
        # of the loop. A larger window that "also matches" is always a multiple
        # of the fundamental period and would under-count repeats.
        window_cap = min(max_window, (n - i) // min_repeats)
        for window in range(min_window, window_cap + 1):
            if hashes[i : i + window] != hashes[i + window : i + 2 * window]:
                continue
            repeats = 2
            j = i + 2 * window
            while j + window <= n and hashes[i : i + window] == hashes[j : j + window]:
                repeats += 1
                j += window
            best_match = (window, repeats)
            break
        if best_match is not None:
            window, repeats = best_match
            loop_start = i
            loop_end = i + window * repeats
            sample = " / ".join(p[:30] for p in paragraphs[i : i + window])
            findings.append({
                "loop_start_index": loop_start,
                "loop_end_index": loop_end,
                "window_size": window,
                "repeats": repeats,
                "sample": sample[:240],
                "severity": "critical",
                "message": (
                    f"[章节循环] 自第{loop_start+1}段起，{window}段为一块，"
                    f"连续复读{repeats}次。疑似生成阶段模型进入循环状态。"
                ),
            })
            i = loop_end
        else:
            i += 1
    return findings


def remove_chapter_text_loops(
    chapter_text: str,
    *,
    min_window: int = _LOOP_MIN_WINDOW,
    max_window: int = _LOOP_MAX_WINDOW,
    min_repeats: int = _LOOP_MIN_REPEATS,
) -> tuple[str, int]:
    """Collapse repeating paragraph blocks to their first occurrence.

    Runs `detect_chapter_text_loop` then drops all paragraphs in repeat
    copies 2..N (keeping only the first copy of each loop). Returns the
    cleaned text and the count of paragraphs removed.
    """
    loops = detect_chapter_text_loop(
        chapter_text,
        min_window=min_window,
        max_window=max_window,
        min_repeats=min_repeats,
    )
    if not loops:
        return chapter_text, 0
    paragraphs = _split_paragraphs(chapter_text)
    drop_indices: set[int] = set()
    for loop in loops:
        w = loop["window_size"]
        start = loop["loop_start_index"]
        end = loop["loop_end_index"]
        # Keep [start, start + w); drop [start + w, end)
        for p in range(start + w, end):
            drop_indices.add(p)
        logger.warning(
            "Removing LLM-loop block: start=%d window=%d repeats=%d (%s)",
            start, w, loop["repeats"], loop["sample"][:80],
        )
    kept = [p for i, p in enumerate(paragraphs) if i not in drop_indices]
    return "\n\n".join(kept), len(drop_indices)


# ---------------------------------------------------------------------------
# 4c. Fuzzy short-line cluster near-repeat detector
# ---------------------------------------------------------------------------
# The block-loop detector above requires *exact* window equality, which misses
# a second LLM failure mode: two adjacent clusters of short lines that share
# most of their paragraphs but have minor insertions/deletions (e.g. chapter
# 181 post-main-loop had two short-line clusters with ~85% overlap but
# different lengths). This detector finds regions dense with short paragraphs
# and drops short paragraphs whose hash was already seen earlier in the
# chapter *inside a similarly dense region* — catching the near-repeat case
# while leaving legitimate short dialogue in narrative-rich regions untouched.

_SHORT_CLUSTER_DENSITY = 0.6    # ≥60% of neighbours must also be short
_SHORT_CLUSTER_WINDOW = 11       # ±5 neighbours = 11-paragraph context window
_SHORT_LINE_MIN_LEN = 3          # ignore 1-2 char fragments ("好", "嗯。")
_ECHO_MAX_DISTANCE = 30          # first occurrence must be within N paragraphs
_ECHO_CLUSTER_WINDOW = 8         # confirmation window — ≥N echoes within this span
_ECHO_CLUSTER_MIN = 2            # need ≥N echoes clustered to confirm loop
                                 # (2 within 8 paras = strong local signal)


def _hash_paragraph(para: str) -> int:
    return int(hashlib.md5(_normalize_text(para).encode()).hexdigest()[:12], 16)


def _short_line_flags(
    paragraphs: list[str],
    *,
    max_paragraph_length: int,
) -> list[bool]:
    return [len(_normalize_text(p)) < max_paragraph_length for p in paragraphs]


def _short_dense_flags(
    short_flags: list[bool],
    *,
    window: int,
    density: float,
) -> list[bool]:
    """Return per-paragraph flag: True when the ±window/2 neighbourhood is
    dominated by short paragraphs."""
    n = len(short_flags)
    half = window // 2
    out = [False] * n
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        slice_ = short_flags[lo:hi]
        if slice_ and (sum(slice_) / len(slice_)) >= density:
            out[i] = short_flags[i]
    return out


def detect_short_cluster_near_repeat(
    chapter_text: str,
    *,
    max_paragraph_length: int = _MIN_PARA_LEN,
    window: int = _SHORT_CLUSTER_WINDOW,
    density: float = _SHORT_CLUSTER_DENSITY,
    min_short_line_len: int = _SHORT_LINE_MIN_LEN,
    max_echo_distance: int = _ECHO_MAX_DISTANCE,
    echo_cluster_window: int = _ECHO_CLUSTER_WINDOW,
    echo_cluster_min: int = _ECHO_CLUSTER_MIN,
) -> list[dict[str, Any]]:
    """Detect short paragraphs that repeat an earlier short paragraph, *both*
    sitting inside a short-line-dense region of the chapter.

    To avoid false positives on legitimate repeated dialogue (character
    catchphrases, motif callbacks), we require:

      1. Both occurrences sit inside a short-line-dense region (window/density).
      2. The echo is within ``max_echo_distance`` paragraphs of the first
         occurrence — real loop artifacts are local, legitimate callbacks
         tend to be far apart.
      3. The echo is part of a CLUSTER of ≥ ``echo_cluster_min`` echoes within
         an ``echo_cluster_window``-paragraph span — isolated single-line
         repeats are preserved as legitimate.

    This is the fuzzy companion to ``detect_chapter_text_loop``: it tolerates
    insertions, deletions, and reordering between the original short cluster
    and its echo(es).
    """
    paragraphs = _split_paragraphs(chapter_text)
    if len(paragraphs) < window:
        return []
    short_flags = _short_line_flags(
        paragraphs, max_paragraph_length=max_paragraph_length
    )
    dense_flags = _short_dense_flags(short_flags, window=window, density=density)

    # Stage 1: collect candidate echoes. Track every short-enough paragraph's
    # position regardless of density — a loop artifact's "first copy" often
    # sits in mixed narrative (not flagged as dense). Only the ECHO position
    # must be inside a dense region to count as a candidate.
    seen: dict[int, list[int]] = {}
    candidates: list[dict[str, Any]] = []
    for i, para in enumerate(paragraphs):
        normalized = _normalize_text(para)
        if len(normalized) < min_short_line_len or len(normalized) >= max_paragraph_length:
            continue
        h = _hash_paragraph(para)
        if h in seen and dense_flags[i]:
            nearest = seen[h][-1]
            if i - nearest <= max_echo_distance:
                candidates.append({
                    "first_pos": nearest,
                    "second_pos": i,
                    "text": para[:120],
                })
        seen.setdefault(h, []).append(i)

    if len(candidates) < echo_cluster_min:
        return []

    # Stage 2: confirm only echoes that are part of a tight cluster. Walk a
    # sliding window over candidate positions; a candidate is confirmed if
    # ≥ echo_cluster_min candidates (including itself) fall within the span.
    echo_positions = [c["second_pos"] for c in candidates]
    confirmed: set[int] = set()
    for idx, pos in enumerate(echo_positions):
        # Count echoes within [pos - window/2, pos + window/2]
        half = echo_cluster_window // 2
        nearby = sum(
            1 for p in echo_positions if pos - half <= p <= pos + half
        )
        if nearby >= echo_cluster_min:
            confirmed.add(pos)

    findings: list[dict[str, Any]] = []
    for c in candidates:
        if c["second_pos"] not in confirmed:
            continue
        findings.append({
            **c,
            "severity": "major",
            "message": (
                f"[短行聚团复读] 第{c['second_pos']+1}段在短行密集区与第"
                f"{c['first_pos']+1}段完全一致，疑似生成阶段模型复读。"
            ),
        })
    return findings


def remove_short_cluster_near_repeats(
    chapter_text: str,
    *,
    max_paragraph_length: int = _MIN_PARA_LEN,
    window: int = _SHORT_CLUSTER_WINDOW,
    density: float = _SHORT_CLUSTER_DENSITY,
    min_short_line_len: int = _SHORT_LINE_MIN_LEN,
) -> tuple[str, int]:
    """Drop short paragraphs identified by ``detect_short_cluster_near_repeat``.

    Keeps the first occurrence inside a dense region. Returns the cleaned
    chapter text and the count of paragraphs removed.
    """
    findings = detect_short_cluster_near_repeat(
        chapter_text,
        max_paragraph_length=max_paragraph_length,
        window=window,
        density=density,
        min_short_line_len=min_short_line_len,
    )
    if not findings:
        return chapter_text, 0
    paragraphs = _split_paragraphs(chapter_text)
    drop_indices = {f["second_pos"] for f in findings}
    for f in findings:
        logger.warning(
            "Removing short-line-cluster near-repeat: para=%d (first at %d) '%s'",
            f["second_pos"], f["first_pos"], f["text"][:40],
        )
    kept = [p for i, p in enumerate(paragraphs) if i not in drop_indices]
    return "\n\n".join(kept), len(drop_indices)


def remove_intra_chapter_duplicates(chapter_text: str) -> tuple[str, int]:
    """Remove duplicate paragraph blocks from an assembled chapter.

    Keeps the FIRST occurrence of each paragraph and discards subsequent
    duplicates. Preserves the chapter heading and blank-line structure.

    Returns
    -------
    (cleaned_text, removed_count)
    """
    paragraphs = _split_paragraphs(chapter_text)
    seen: set[int] = set()
    kept: list[str] = []
    removed = 0

    for para in paragraphs:
        normalized = _normalize_text(para)
        if len(normalized) < _MIN_PARA_LEN:
            kept.append(para)
            continue
        key = int(hashlib.md5(normalized.encode()).hexdigest()[:12], 16)
        if key in seen:
            removed += 1
            logger.warning(
                "Removing duplicate paragraph (len=%d): %s…",
                len(para), para[:60].replace("\n", " "),
            )
        else:
            seen.add(key)
            kept.append(para)

    return "\n\n".join(kept), removed


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


# ---------------------------------------------------------------------------
# 5. Hook / cliffhanger ending repetition check
# ---------------------------------------------------------------------------

def check_hook_repetition(
    new_hook: str,
    existing_hooks: list[tuple[int, str]],
    *,
    similarity_threshold: float = 0.75,
    hook_length: int = 200,
) -> list[dict[str, Any]]:
    """Check if a chapter's cliffhanger ending is too similar to recent hooks.

    Parameters
    ----------
    new_hook : str
        Last ``hook_length`` characters of the new chapter.
    existing_hooks : list of (chapter_number, hook_text)
        Recent chapter endings to compare against.
    similarity_threshold : float
        Jaccard similarity above this triggers a finding.
    hook_length : int
        How many trailing characters to compare.
    """
    if not new_hook:
        return []

    new_text = _normalize_text(new_hook[-hook_length:])
    findings: list[dict[str, Any]] = []

    for ch_num, existing_hook in existing_hooks:
        if not existing_hook:
            continue
        existing_text = _normalize_text(existing_hook[-hook_length:])
        similarity = compute_jaccard_similarity(new_text, existing_text)
        if similarity >= similarity_threshold:
            findings.append({
                "chapter": ch_num,
                "similarity": round(similarity, 3),
                "severity": "major",
                "message": (
                    f"[钩子重复] 本章结尾与第{ch_num}章结尾相似度为 {similarity:.1%}，"
                    f"请改写收尾以增加差异性。"
                ),
            })

    return findings


# ---------------------------------------------------------------------------
# 6. Meta-text marker cleanup
# ---------------------------------------------------------------------------

# Patterns that are author/tool notes leaked into chapter prose.
_META_TEXT_PATTERNS = [
    # "——\n\n**第N章 完**" — separator + bold chapter-end marker (must match first)
    re.compile(r"\n——\n\n\*\*第\d+章[^*\n]{0,20}\*\*\s*", re.MULTILINE),
    # "**第28章 完**" style Markdown bold markers
    re.compile(r"\n\*\*第\d+章[^*\n]{0,20}\*\*\s*", re.MULTILINE),
    # "（本章完）" or "(本章完)" anywhere in text
    re.compile(r"\n?[（(]本章完[）)]\s*", re.MULTILINE),
    # "（全文完）" novel-end markers
    re.compile(r"\n?[（(]全文完[）)]\s*", re.MULTILINE),
    # Trailing "——" separator left after marker removal
    re.compile(r"\n——\s*$", re.MULTILINE),
]


def clean_meta_text_markers(text: str) -> tuple[str, int]:
    """Remove author/tool meta-text markers that leaked into chapter prose.

    Returns
    -------
    (cleaned_text, removed_count)
        cleaned_text — text with all meta markers stripped.
        removed_count — number of markers removed (0 if text was clean).
    """
    removed = 0
    for pattern in _META_TEXT_PATTERNS:
        new_text, n = pattern.subn("", text)
        if n:
            removed += n
            text = new_text
    return text.rstrip() + "\n" if removed else text, removed


# ---------------------------------------------------------------------------
# 7. Opening diversity prompt block
# ---------------------------------------------------------------------------

def build_opening_diversity_block(
    recent_openings: list[tuple[int, str]],
    *,
    language: str = "zh-CN",
    opening_length: int = 60,
) -> str:
    """Render a prompt block listing recent chapter openings to avoid duplicating.

    Parameters
    ----------
    recent_openings : list of (chapter_number, opening_text)
        The last N chapters' first non-heading lines.
    language : str
        Language code for prompt language selection.
    opening_length : int
        How many characters of each opening to show.
    """
    if not recent_openings:
        return ""

    is_zh = language.lower().startswith("zh")

    if is_zh:
        lines = ["【最近章节开头 — 本章必须使用不同的开场方式】"]
        for ch_num, opening in recent_openings:
            snippet = opening[:opening_length].replace("\n", " ")
            lines.append(f"• 第{ch_num}章开头：「{snippet}…」")
        lines.append("开场不得重复上述任何场景设定、视角切入或句式结构。")
    else:
        lines = ["[RECENT CHAPTER OPENINGS — this chapter MUST open differently]"]
        for ch_num, opening in recent_openings:
            snippet = opening[:opening_length].replace("\n", " ")
            lines.append(f"• Ch{ch_num} opened with: \"{snippet}…\"")
        lines.append(
            "Do NOT start with the same noun phrase, setting, or sentence structure. "
            "Especially avoid starting with \"The [noun] [verb]...\" if that pattern appears above."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. Conflict / scene-purpose / environment diversity blocks
#    (Stage A + Stage B of the story-quality deep-optimization plan)
# ---------------------------------------------------------------------------

def build_conflict_diversity_block(
    recent_conflicts: list[dict[str, Any]],
    *,
    genre_pool_key: str | None = None,
    inject_emerging: bool = False,
    language: str = "zh-CN",
) -> str:
    """Render a prompt block listing recent conflict tuples to avoid duplicating.

    Parameters
    ----------
    recent_conflicts : list[dict]
        Most-recent-first list of conflict-tuple dicts from SceneCard.metadata_json.
        Each dict should have keys: object / layer / nature / resolvability /
        optional conflict_id.
    genre_pool_key : str | None
        Key into `GENRE_POOLS` to surface candidate conflicts for the novel's
        type (e.g. "female_lead_no_cp").
    inject_emerging : bool
        When True, append an emerging-conflict option (triggered by cadence).
    """
    from bestseller.services.conflict_taxonomy import (
        ConflictTuple,
        EMERGING_POOL,
        candidate_pool_for_genre,
        evaluate_switching_rules,
        label_for_layer,
        label_for_nature,
        label_for_object,
    )

    parsed: list[ConflictTuple] = []
    for item in recent_conflicts:
        t = ConflictTuple.from_dict(item)
        if t is not None:
            parsed.append(t)

    is_zh = language.lower().startswith("zh")
    rules = evaluate_switching_rules(parsed)

    if is_zh:
        lines = ["【本场冲突多样性约束（Stage A）】"]
        if not parsed:
            lines.append("• 近场尚无冲突签名 — 本场请自由选取，但需为后续建立可切换的基线。")
        else:
            recent_view = ", ".join(
                f"({label_for_object(t.object)}/{label_for_layer(t.layer)}/{label_for_nature(t.nature)}"
                + (f"·{t.conflict_id}" if t.conflict_id else "")
                + ")"
                for t in parsed[:5]
            )
            lines.append(f"• 近 {len(parsed[:5])} 场冲突签名：{recent_view}")
            if rules["forbid_object"]:
                lines.append(
                    "• 禁用【对抗对象】（连续重复）："
                    + "、".join(label_for_object(k) for k in rules["forbid_object"])
                )
            if rules["forbid_layer"]:
                lines.append(
                    "• 禁用【冲突层次】（连续重复）："
                    + "、".join(label_for_layer(k) for k in rules["forbid_layer"])
                )
            if rules["forbid_nature"]:
                lines.append(
                    "• 禁用【冲突性质】（连续重复）："
                    + "、".join(label_for_nature(k) for k in rules["forbid_nature"])
                )
            if rules["forbid_conflict_id"]:
                lines.append(
                    "• 禁止复用已在近 10 场出现 ≥3 次的 conflict_id："
                    + "、".join(str(c) for c in rules["forbid_conflict_id"])
                )
            if rules["needs_internal"]:
                lines.append(
                    "• 近 5 场缺少内在层冲突 — 本场必须触及 inner_desire 或 inner_identity（至少作为次冲突）"
                )
            lines.append("• Axis A（对抗对象）或 Axis B（冲突层次）至少必须切换其一。")

        pool = candidate_pool_for_genre(genre_pool_key)
        if pool:
            lines.append(
                "• 本书类型冲突候选池（优先从中选一种作为主冲突，或复合）："
                + "、".join(pool)
            )
        if inject_emerging:
            lines.append(
                "• 【Emerging 冲突注入窗口】本场可考虑引入前沿冲突之一："
                + "、".join(EMERGING_POOL)
            )
        lines.append(
            "• 场景结束时必须能给 scene_contract 填入 conflict_tuple "
            "(object, layer, nature, resolvability)，供后续差异化。"
        )
    else:
        lines = ["[SCENE CONFLICT DIVERSITY CONSTRAINTS]"]
        if not parsed:
            lines.append("• No prior conflict signatures — pick freely but establish a switchable baseline.")
        else:
            lines.append(f"• Last {len(parsed[:5])} scenes' conflict tuples:")
            for t in parsed[:5]:
                line = f"   - ({t.object}/{t.layer}/{t.nature})"
                if t.conflict_id:
                    line += f" id={t.conflict_id}"
                lines.append(line)
            if rules["forbid_object"]:
                lines.append(f"• FORBID Axis A (object): {', '.join(rules['forbid_object'])}")
            if rules["forbid_layer"]:
                lines.append(f"• FORBID Axis B (layer): {', '.join(rules['forbid_layer'])}")
            if rules["forbid_nature"]:
                lines.append(f"• FORBID Axis C (nature): {', '.join(rules['forbid_nature'])}")
            if rules["forbid_conflict_id"]:
                lines.append(
                    f"• FORBID reusing these conflict_ids (≥3 in last 10): "
                    f"{', '.join(str(c) for c in rules['forbid_conflict_id'])}"
                )
            if rules["needs_internal"]:
                lines.append(
                    "• Last 5 scenes lack an inner_* layer — MUST include an inner_desire / inner_identity beat."
                )
            lines.append("• Axis A OR Axis B MUST change from the last scene.")
        pool = candidate_pool_for_genre(genre_pool_key)
        if pool:
            lines.append(f"• Genre-specific candidate pool: {', '.join(pool)}")
        if inject_emerging:
            lines.append(f"• Emerging-conflict window — consider one of: {', '.join(EMERGING_POOL)}")

    return "\n".join(lines)


def build_scene_purpose_diversity_block(
    recent_purposes: list[str],
    *,
    language: str = "zh-CN",
) -> str:
    """Render a prompt block listing recent scene purposes + required family switches."""
    from bestseller.services.scene_taxonomy import (
        PURPOSE_FAMILIES,
        evaluate_purpose_rules,
        purpose_label,
    )

    is_zh = language.lower().startswith("zh")
    rules = evaluate_purpose_rules(recent_purposes)
    family_label_zh = {
        "A_structural": "A 结构位类",
        "B_action": "B 动作推进类",
        "C_relation": "C 信息关系类",
        "D_interior": "D 内在节奏类",
    }

    if is_zh:
        lines = ["【本场场景目的多样性约束（Stage B · 场景目的）】"]
        if recent_purposes:
            shown = ", ".join(
                f"{p}({purpose_label(p)})" for p in recent_purposes[:5] if p
            )
            lines.append(f"• 近 5 场 purpose：{shown or '（无）'}")
        else:
            lines.append("• 近场尚无 purpose 记录。")
        if rules["forbid_purposes"]:
            lines.append(
                "• 本场禁用上述 5 场内已出现的 purpose："
                + "、".join(rules["forbid_purposes"])
            )
        if rules["underused_families"]:
            lines.append(
                "• 本场**优先**从以下未覆盖族中选取 purpose："
                + "、".join(family_label_zh.get(f, f) for f in rules["underused_families"])
            )
        pool = rules["candidate_pool"][:12]
        if pool:
            lines.append(
                "• 候选 purpose（从中选一作为本场主轴，必要时可复合两个跨族 purpose）："
                + "、".join(pool)
            )
        lines.append(
            "• 场景结束时在 scene_contract.metadata_json.scene_purpose_id "
            "写入本场实际使用的 purpose id，供下一场约束。"
        )
    else:
        lines = ["[SCENE PURPOSE DIVERSITY CONSTRAINTS]"]
        if recent_purposes:
            lines.append(f"• Last 5 purposes: {', '.join(recent_purposes[:5])}")
        if rules["forbid_purposes"]:
            lines.append(f"• FORBID purposes: {', '.join(rules['forbid_purposes'])}")
        if rules["underused_families"]:
            lines.append(
                f"• PREFER underused families: {', '.join(rules['underused_families'])}"
            )
        lines.append(f"• Candidate pool: {', '.join(rules['candidate_pool'][:12])}")

    return "\n".join(lines)


def build_env_diversity_block(
    recent_envs: list[dict[str, Any]],
    *,
    language: str = "zh-CN",
) -> str:
    """Render a prompt block describing the prior scene's 7-d env and required deltas."""
    from bestseller.services.scene_taxonomy import (
        ENV_DIMENSIONS,
        EnvVector,
        env_label_zh,
        evaluate_env_rules,
    )

    parsed = [EnvVector.from_dict(e) for e in recent_envs if e]
    rules = evaluate_env_rules(parsed)
    is_zh = language.lower().startswith("zh")

    dim_label_zh = {
        "physical_space": "物理空间",
        "time_of_day": "时间段",
        "weather_light": "天气/光照",
        "dominant_sense": "感官主导",
        "social_density": "社交密度",
        "tempo_scale": "节奏尺度",
        "vertical_enclosure": "垂直封闭度",
    }

    if is_zh:
        lines = ["【本场环境 7 维切换约束（Stage B · 环境）】"]
        if not parsed:
            lines.append("• 近场尚无环境签名 — 本场请在 scene metadata 中写入 7 维取值。")
        else:
            prev = parsed[0]
            shown = "、".join(
                f"{dim_label_zh[d]}:{env_label_zh(d, getattr(prev, d))}"
                for d in ENV_DIMENSIONS
            )
            lines.append(f"• 上一场 7 维：{shown}")
            lines.append(
                f"• 本场相对上一场必须至少 {rules['min_diff_vs_prev']} 维不同，"
                f"且相对近 3 场任一场至少 {rules['min_diff_vs_any_of_prev3']} 维不同。"
            )
            lines.append(
                "• 优先切换「物理空间 / 时间段 / 天气光照」这三维——视觉冲击最大；"
                "其次切「感官主导」——让不同感官引领段落节奏。"
            )
            lines.append(
                "• 若与上一场同一地点（复访），必须同时：(1) 切换价值轴 "
                "(生死/信任/身份/权力 四者之一)，(2) 切换感官通道 + 时间天气，"
                "(3) 切换社交拓扑或该地点的功能角色（通道→终点，藏身处→祭坛…）。"
            )
    else:
        lines = ["[SCENE ENVIRONMENT DIVERSITY CONSTRAINTS]"]
        if parsed:
            prev = parsed[0]
            shown = ", ".join(f"{d}={getattr(prev, d)}" for d in ENV_DIMENSIONS)
            lines.append(f"• Previous 7-d env: {shown}")
            lines.append(
                f"• New scene must differ in ≥{rules['min_diff_vs_prev']} dims vs prev "
                f"and ≥{rules['min_diff_vs_any_of_prev3']} vs any of last 3."
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 9. Stage C — character-arc beat block + 5-layer thinking contract
# ---------------------------------------------------------------------------

def build_arc_beat_block(
    pov_inner_structure: dict[str, Any] | None,
    *,
    chapter_number: int,
    total_chapters: int,
    pov_name: str | None = None,
    language: str = "zh-CN",
) -> str:
    """Render a prompt block describing the POV character's current arc beat.

    Parameters
    ----------
    pov_inner_structure : dict | None
        CharacterInnerStructure.as_dict() payload for the POV character, or
        None when the planner has not yet filled it in.
    chapter_number / total_chapters : int
        Used to compute the current beat via the percentile beat table.
    pov_name : str | None
        Display name of the POV character (Chinese or English).
    """
    from bestseller.services.character_arcs import (
        ARC_TYPES,
        CharacterInnerStructure,
        compute_arc_stage_for_chapter,
    )

    is_zh = language.lower().startswith("zh")
    stage = compute_arc_stage_for_chapter(chapter_number, total_chapters)

    if is_zh:
        lines = ["【本章 POV 人物弧 / 内在结构（Stage C）】"]
        lines.append(
            f"• 章节进度：{stage['percentile']*100:.1f}% "
            f"（主 beat：{stage['primary_beat'] or '-'}）"
        )
        if stage["active_beats_description"]:
            lines.append(
                "• 当前活跃 beat："
                + "；".join(stage["active_beats_description"])
            )
        if pov_inner_structure:
            s = CharacterInnerStructure.from_dict(pov_inner_structure)
            name_display = pov_name or "POV 角色"
            arc_label = ARC_TYPES.get(s.arc_type, s.arc_type)
            lines.append(f"• {name_display} 弧型：{arc_label}")
            if s.lie_believed:
                lines.append(f"• 她相信的谎言（lie）：{s.lie_believed}")
            if s.truth_to_learn:
                lines.append(f"• 她必须学到的真相（truth）：{s.truth_to_learn}")
            if s.want_external:
                lines.append(f"• 表层目标（want）：{s.want_external}")
            if s.need_internal:
                lines.append(f"• 内在需要（need）：{s.need_internal}")
            if s.ghost:
                lines.append(f"• 过往伤痕（ghost）：{s.ghost}")
            if s.fatal_flaw:
                lines.append(f"• 致命缺陷（flaw）：{s.fatal_flaw}")
            if s.fear_core:
                lines.append(f"• 核心恐惧：{s.fear_core}")
            if s.defense_mechanisms:
                lines.append("• 防御机制：" + "、".join(s.defense_mechanisms))
            lines.append(
                "• 本章/本场必须让上述 lie ↔ truth 之间发生一次可见的摩擦，"
                "不许只「获取信息」；必须有「价值观被震动」的一笔。"
            )
        else:
            lines.append(
                "• 尚无 POV 内在结构 — 本场必须在 POV 段内展现"
                "「她相信的东西 → 被现实挑战 → 半推半就地调整」三拍的至少一拍。"
            )
        if stage["primary_beat"] in {"regression", "dark_night", "epiphany"}:
            lines.append(
                f"• ⚠️ 当前处于弧线裂缝期（{stage['primary_beat']}）— "
                "必须写出「可是……」的内部裂纹，不许角色继续用旧逻辑顺滑运行。"
            )
    else:
        lines = ["[POV CHARACTER ARC + INNER STRUCTURE — Stage C]"]
        lines.append(
            f"• Story percentile: {stage['percentile']*100:.1f}% "
            f"(primary beat: {stage['primary_beat'] or '-'})"
        )
        if pov_inner_structure:
            s = CharacterInnerStructure.from_dict(pov_inner_structure)
            lines.append(f"• Arc type: {s.arc_type}")
            for field_key, label in (
                ("lie_believed", "LIE"),
                ("truth_to_learn", "TRUTH"),
                ("want_external", "WANT"),
                ("need_internal", "NEED"),
                ("ghost", "GHOST"),
                ("fatal_flaw", "FLAW"),
                ("fear_core", "FEAR"),
            ):
                value = getattr(s, field_key)
                if value:
                    lines.append(f"• {label}: {value}")
            lines.append(
                "• The scene MUST stage a visible friction between LIE and TRUTH; "
                "information retrieval alone is NOT enough — a value must wobble."
            )
        else:
            lines.append(
                "• POV inner structure not yet filled — in the POV section, "
                "show a mini believed-truth → challenged → reluctantly-adjusted arc."
            )

    return "\n".join(lines)


def build_five_layer_thinking_block(*, language: str = "zh-CN") -> str:
    """Thin wrapper around `render_five_layer_block` so the contract can be
    budgeted like any other Tier-1 block."""
    from bestseller.services.character_arcs import render_five_layer_block

    return render_five_layer_block(language=language)


# ---------------------------------------------------------------------------
# 10. Stage D — cliffhanger diversity + tension target
# ---------------------------------------------------------------------------

def build_cliffhanger_diversity_block(
    recent_hook_types: list[str | None],
    *,
    chapter_number: int,
    total_chapters: int,
    language: str = "zh-CN",
) -> str:
    """Render a prompt block listing forbidden hook types + suggested ones."""
    from bestseller.services.pacing_engine import (
        CLIFFHANGER_TYPES,
        evaluate_hook_diversity,
    )

    is_zh = language.lower().startswith("zh")
    result = evaluate_hook_diversity(recent_hook_types)

    if is_zh:
        lines = ["【章末钩子多样性约束（Stage D · 钩子）】"]
        if recent_hook_types:
            shown = "、".join(
                (h or "—") for h in recent_hook_types[:5]
            )
            lines.append(f"• 最近 5 章钩子类型：{shown}")
        else:
            lines.append("• 近章尚无钩子记录 — 本章可自由选取。")
        if result["forbid"]:
            lines.append(
                "• 本章禁用钩子类型："
                + "、".join(
                    f"{k}（{CLIFFHANGER_TYPES.get(k, k)}）"
                    for k in result["forbid"]
                )
            )
        if result["suggested"]:
            lines.append(
                "• 建议优先使用的钩子类型（按欠账排序）："
                + "、".join(
                    f"{k}（{CLIFFHANGER_TYPES.get(k, k)}）"
                    for k in result["suggested"]
                )
            )
        lines.append(
            "• 章节结尾必须在 chapter_contract.metadata_json.hook_type "
            "写入 7 类钩子之一：suspense/twist/crisis/revelation/sudden/emotional/philosophical。"
        )
    else:
        lines = ["[CLIFFHANGER DIVERSITY CONSTRAINTS — Stage D]"]
        if recent_hook_types:
            lines.append(
                "• Last 5 hook types: "
                + ", ".join((h or "—") for h in recent_hook_types[:5])
            )
        if result["forbid"]:
            lines.append(f"• FORBID hook types: {', '.join(result['forbid'])}")
        if result["suggested"]:
            lines.append(
                f"• Suggested hooks (by under-use): {', '.join(result['suggested'])}"
            )
        lines.append(
            "• At chapter end, record hook_type in "
            "chapter_contract.metadata_json.hook_type (one of 7 canonical types)."
        )

    return "\n".join(lines)


def build_tension_target_block(
    chapter_number: int,
    total_chapters: int,
    *,
    recent_tension_scores: list[float] | None = None,
    language: str = "zh-CN",
) -> str:
    """Render a prompt block giving the target tension + flat-rhythm warning."""
    from bestseller.services.pacing_engine import (
        evaluate_tension_variance,
        target_beat_for_chapter,
    )

    is_zh = language.lower().startswith("zh")
    entry = target_beat_for_chapter(chapter_number, total_chapters)

    if is_zh:
        lines = ["【本章张力目标（Stage D · 节拍）】"]
        lines.append(
            f"• 节拍位：{entry.beat_name} — {entry.notes}"
        )
        lines.append(
            f"• 目标章节张力（0-10）：约 {entry.tension_target:.1f}"
            "（鼓励 ±1.0 内的波动，避免与相邻章相同）"
        )
    else:
        lines = ["[CHAPTER TENSION TARGET — Stage D]"]
        lines.append(f"• Beat position: {entry.beat_name} — {entry.notes}")
        lines.append(
            f"• Target chapter tension (0-10): ~{entry.tension_target:.1f} "
            "(±1.0 variance preferred vs adjacent chapters)"
        )

    if recent_tension_scores:
        variance = evaluate_tension_variance(recent_tension_scores)
        if variance.get("flag_flat"):
            if is_zh:
                lines.append(
                    f"• ⚠️ 近 {len(recent_tension_scores[:10])} 章张力标准差 "
                    f"{variance['std']} < 1.5 — 剧情节奏趋于扁平！"
                    "本章必须给张力曲线打一个不同方向的拐点。"
                )
            else:
                lines.append(
                    f"• ⚠️ Recent tension std={variance['std']} is < 1.5 — "
                    "rhythm is flat. Steer this chapter to a distinct tension direction."
                )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 11. Location ledger — same-location reuse cap + emotional reframe
# ---------------------------------------------------------------------------

def build_location_ledger_block(
    current_location: str | None,
    recent_scene_locations: list[str | None],
    *,
    language: str = "zh-CN",
    visit_cap: int = 4,
) -> str:
    """Render a prompt block enforcing same-location reframe + visit cap."""
    from bestseller.services.scene_taxonomy import location_visit_count

    is_zh = language.lower().startswith("zh")
    visits_so_far = location_visit_count(current_location, recent_scene_locations)

    if is_zh:
        lines = ["【地点复访约束（Stage B+ · 地点账本）】"]
        if current_location:
            lines.append(f"• 本场地点：{current_location}（历史已访问 {visits_so_far} 次）")
            if visits_so_far >= visit_cap:
                lines.append(
                    f"• ⚠️ 该地点已达上限（≥{visit_cap} 次）— 本场必须迁移到新地点，"
                    "或在同地点中引入**明显不同**的功能定位（如：通道→祭坛、藏身处→审判席）。"
                )
            elif visits_so_far >= 1:
                lines.append(
                    "• 复访规则（必须全部满足）：\n"
                    "    (1) 切换**价值轴**（生死 / 信任 / 身份 / 权力 四选一，与上次不同）；\n"
                    "    (2) 切换**感官通道**（上次视觉主导，本次改嗅觉/触觉/听觉）；\n"
                    "    (3) 切换**社交拓扑**（独处 ↔ 二人 ↔ 多人 ↔ 无人空场）或地点的**功能角色**。"
                )
            else:
                lines.append("• 新地点 — 建立该地点的**原始感官签名**（3 条具象细节，后续复访可对照）。")
        else:
            lines.append("• 本场地点未定 — 请优先选一处近 5 场未使用的地点。")
        recent_trimmed = [loc for loc in recent_scene_locations[:8] if loc]
        if recent_trimmed:
            lines.append("• 近 8 场地点序列：" + " → ".join(recent_trimmed))
    else:
        lines = ["[LOCATION LEDGER — same-location reframe + visit cap]"]
        if current_location:
            lines.append(
                f"• Current location: {current_location} (visited {visits_so_far}× recently)"
            )
            if visits_so_far >= visit_cap:
                lines.append(
                    f"• ⚠️ Visit cap reached (≥{visit_cap}) — move to a new location "
                    "or shift the location's FUNCTION entirely (corridor→altar, hideout→dock)."
                )
            elif visits_so_far >= 1:
                lines.append(
                    "• Revisit rules (ALL must hold): (1) new value axis; "
                    "(2) new dominant sense; (3) new social topology or functional role."
                )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Intra-chapter stitched-draft detection
#
# ``detect_chapter_text_loop`` catches verbatim paragraph repeats (a writer
# bug). ``detect_intra_chapter_stitched_drafts`` catches a much subtler issue:
# the same plot event re-told twice in the same chapter with DIFFERENT prose
# (a *merge* bug, typical when two LLM candidates were both concatenated into
# the final draft). Detection is event-signature based, not text-similarity
# based.
# ---------------------------------------------------------------------------

# Action verbs that strongly mark "plot events" (Chinese-first)
_EVENT_ACTION_VERBS = (
    "撬", "开", "掏", "塞", "拿", "拾", "取", "夺", "递", "抛", "藏",
    "推门", "踏入", "闯入", "潜入", "翻窗",
    "打飞", "震飞", "击退", "炸开", "斩", "刺", "刺出", "拍出", "掌出",
    "围", "围住", "拦", "截", "扑", "袭", "突袭",
    "撞见", "现身", "现身", "出现", "落地", "落下",
    "封", "封死", "封住", "锁", "缚",
    "晕", "倒", "跪", "瘫", "退",
    "看见", "看清", "认出", "察觉",
)

# Strong "physical object" props characteristic to the genre; reusing one in
# two distinct prose blocks within a single chapter is a stitched-draft tell.
_EVENT_PROP_WORDS = (
    "暗格", "玉简", "册子", "残篇", "令牌", "符纸", "符文", "丹炉", "锦盒",
    "灵镜", "镜面", "剑", "斧", "刀", "鞭", "锤", "弓", "盾",
    "丹药", "灵草", "灵石", "禁制", "封印",
    "灯", "烛", "镜", "符",
)


@dataclass(frozen=True)
class EventSignature:
    """A coarse fingerprint of an in-chapter prose block.

    Two blocks with overlapping signatures (same participants + same prop
    word + same action verb cluster) are very likely two drafts of the same
    plot beat. We do *not* fingerprint by text similarity, since the
    distinguishing failure mode here is paraphrased re-runs.
    """

    block_index: int
    char_offset: int
    participants: frozenset[str]
    props: frozenset[str]
    verbs: frozenset[str]
    excerpt: str  # first 80 chars for evidence


@dataclass(frozen=True)
class StitchedDraftFinding:
    """A pair of blocks that look like alternative drafts of the same beat."""

    block_a: EventSignature
    block_b: EventSignature
    similarity: float
    conflicts: tuple[str, ...]  # short prose descriptions of contradictions


def _split_into_event_blocks(chapter_text: str) -> list[tuple[int, str]]:
    """Split a chapter into "event blocks" (scenes).

    Strategy:
    1. Strip YAML frontmatter if present.
    2. Strip H1/H2 headings (chapter title lines).
    3. Prefer ``---`` scene separators -- the canonical scene boundary in
       this codebase's chapter markdown. Each block between ``---`` lines
       is one event scene.
    4. Fall back to double-newline paragraph splitting when no ``---``
       separators exist in the body.

    Returns ``[(char_offset, text)]``. Skips blocks shorter than 90 non-newline
    chars, since they cannot host a full event beat (typical false-positive
    sources: one-line dialogue, scene-setting lines).
    """
    if not chapter_text:
        return []

    # Drop YAML frontmatter if present
    text = chapter_text
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            text = text[end + 3 :]

    # Drop H1/H2 chapter title lines
    text = re.sub(r"^#{1,6}\s.*$", "", text, flags=re.MULTILINE)

    # Strategy: split on ``---`` scene separators when present. Each segment
    # then has whatever paragraphing it had internally; we concatenate it as
    # one block of "narrative material" for the same scene.
    has_scene_sep = bool(re.search(r"\n\s*---\s*\n", text))

    blocks: list[tuple[int, str]] = []
    if has_scene_sep:
        cursor = 0
        for segment in re.split(r"\n\s*---\s*\n", text):
            seg = segment.strip()
            char_offset = text.find(segment, cursor)
            cursor = char_offset + len(segment) if char_offset >= 0 else cursor
            if not seg:
                continue
            if len(seg.replace("\n", "")) < 90:
                continue
            blocks.append((char_offset, seg))
        return blocks

    # Fall back to paragraph-level split with an accumulator so chapters that
    # use many short paragraphs (typical for fast-paced action) still surface
    # event blocks of meaningful length.
    cursor = 0
    pending: list[str] = []
    pending_offset: int = -1
    pending_chars: int = 0
    BLOCK_TARGET_CHARS = 220     # commit when accumulator >= this
    MIN_BLOCK_CHARS = 90         # ignore final scraps below this

    def _commit() -> None:
        nonlocal pending, pending_offset, pending_chars
        if pending_chars >= MIN_BLOCK_CHARS and pending_offset >= 0:
            blocks.append((pending_offset, "\n\n".join(pending).strip()))
        pending = []
        pending_offset = -1
        pending_chars = 0

    for paragraph in re.split(r"\n\s*\n", text):
        para = paragraph.strip()
        char_offset = text.find(paragraph, cursor)
        cursor = char_offset + len(paragraph) if char_offset >= 0 else cursor
        if not para or para.startswith("#") or para.startswith("---"):
            continue
        plen = len(para.replace("\n", ""))
        if pending_offset < 0:
            pending_offset = char_offset
        pending.append(para)
        pending_chars += plen
        if pending_chars >= BLOCK_TARGET_CHARS:
            _commit()
    _commit()
    return blocks


# Common 2-3 char prose tokens that LOOK like names but aren't. Critical
# denylist -- without it, location words and adverbs are pooled as "names" and
# wreck the participant signature.
_NAME_STOPWORDS = frozenset({
    # Pronouns / collective references
    "他们", "她们", "你们", "我们", "自己", "别人", "众人", "众弟", "那人", "这人",
    "有人", "无人",
    # Location words that can appear standalone
    "门口", "门外", "屋内", "屋外", "丹房", "藏经", "演武", "宿舍", "院子", "山门",
    # Adverbs / connectors
    "庄严", "突然", "忽然", "刚才", "片刻", "片晌", "瞬间", "果然", "终于", "原来",
    "其实", "其中", "另一", "其他", "其余", "那个", "这个", "什么", "怎么", "怎样",
    "为什么", "怎么办", "没料", "没想", "想到", "似乎", "仿佛", "宛如", "好像",
    "还是", "不是", "不能", "不行", "不要", "不知", "已经", "现在", "依然", "依旧",
    "果真", "还有", "再次", "再来", "一道", "一片", "一阵", "一种", "一道",
    "像是", "像一", "像被", "像有", "像在", "如同", "犹如", "如是", "如此",
    "可以", "可能", "应该", "或许", "也许", "大概", "也是", "便是",
    # Time / lighting
    "月光", "晨光", "夜色", "黄昏", "傍晚", "深夜", "黎明",
    # Body / location prepositionals
    "掌中", "袖中", "胸中", "心中", "手中", "怀中", "眼中", "嘴中", "口中",
    "头顶", "身前", "身后", "身上", "身侧", "脚下", "面前", "眼前", "面上",
    "腰间", "肩头", "肩上", "膝上",
    # Body parts that often start sentences (POV anchors, not names)
    "丹田", "掌心", "指尖", "指节", "胸口", "嘴角", "目光", "眉头", "眉宇", "肩膀",
    "脸色", "脖颈", "喉咙", "心脏", "心头",
    # Cultivation / setting jargon ubiquitous in xianxia — would otherwise
    # dominate the pool. NOTE: real character names like 道君 / 道祖 are
    # whitelisted via the project's character-aliases.yaml when present.
    "道种", "道典", "灵压", "灵气", "灵力", "灵根", "筑基", "炼气", "金丹", "元婴",
    "封面", "封印", "封禁", "符文", "阴阳", "按律", "照出", "宗门", "禁地",
})

# Chars that mark the *left* boundary of a name in vernacular CJK prose.
_NAME_BOUNDARY_CHARS = set("\n\t 　，。：；！？、「」『』\"\"''（）()【】…·—-")


def _extract_chapter_name_pool(chapter_text: str, min_occurrences: int = 2) -> set[str]:
    """Surface 2-3 char tokens that look like character names in this chapter.

    Algorithm:
    1. Walk the text char by char.
    2. At each position preceded by a boundary character (punctuation /
       whitespace / start-of-text), try to grab the next 2-3 contiguous Han
       chars as a candidate name.
    3. Pool tokens that appear ≥ ``min_occurrences`` times and aren't in
       the stopword set.

    This is intentionally simpler than a regex lookbehind/lookahead because
    Chinese names can be followed by *any* verb -- there is no compact right-
    boundary character class. The chapter-wide frequency filter handles the
    false-positive problem (one-off prose tokens get dropped).
    """
    if not chapter_text:
        return set()

    counts: dict[str, int] = {}
    text = chapter_text
    n = len(text)
    for i in range(n):
        if i > 0 and text[i - 1] not in _NAME_BOUNDARY_CHARS:
            continue
        # Pool both 2- and 3-char candidates so we can keep canonically
        # 3-char names like 周元青 alongside 2-char names like 宁尘.
        for length in (2, 3):
            end = i + length
            if end > n:
                continue
            candidate = text[i:end]
            if not all("一" <= c <= "鿿" for c in candidate):
                continue
            if candidate in _NAME_STOPWORDS:
                continue
            counts[candidate] = counts.get(candidate, 0) + 1

    # Suppress 3-char candidates that are just a 2-char name + 1-char verb
    # particle. Heuristic: if "宁尘被" appears with count C3 and "宁尘"
    # appears with count C2 >= C3, the 3-char form is almost certainly
    # spurious. We keep "周元青" because 周元 is typically a different
    # character (or doesn't appear) -- so the 3-char dominates.
    dropped: set[str] = set()
    for token in list(counts):
        if len(token) != 3:
            continue
        prefix = token[:2]
        if prefix in _NAME_STOPWORDS:
            dropped.add(token)
        elif prefix in counts and counts[prefix] >= counts[token]:
            dropped.add(token)
    for t in dropped:
        del counts[t]

    return {t for t, c in counts.items() if c >= min_occurrences}


def _signature_of(
    block_idx: int,
    char_offset: int,
    text: str,
    chapter_name_pool: frozenset[str],
) -> EventSignature:
    # Participants = chapter-wide name pool ∩ names appearing in this block.
    participants: set[str] = set()
    for name in chapter_name_pool:
        if name in text:
            participants.add(name)

    props = {w for w in _EVENT_PROP_WORDS if w in text}
    verbs = {v for v in _EVENT_ACTION_VERBS if v in text}

    excerpt = text.replace("\n", " ")[:80].strip()
    return EventSignature(
        block_index=block_idx,
        char_offset=char_offset,
        participants=frozenset(participants),
        props=frozenset(props),
        verbs=frozenset(verbs),
        excerpt=excerpt,
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _event_similarity(a: EventSignature, b: EventSignature) -> float:
    """Weighted similarity: participants 0.55 + props 0.45.

    Verbs were dropped from the original formula -- the candidate verb set
    in deduplication.py is open-ended enough that two drafts of the *same*
    event often share only a couple of verbs (different prose word choices),
    making verbs a noisy signal. The participant + prop signature carries
    almost all the discriminative information for stitched drafts.
    """
    return (
        _jaccard(a.participants, b.participants) * 0.55
        + _jaccard(a.props, b.props) * 0.45
    )


def _list_conflicts(a: EventSignature, b: EventSignature) -> tuple[str, ...]:
    """Surface human-readable conflicts to aid editor's deletion decision."""
    conflicts: list[str] = []
    only_a_props = a.props - b.props
    only_b_props = b.props - a.props
    if only_a_props or only_b_props:
        conflicts.append(
            f"道具差异：A 用了 {sorted(only_a_props) or '∅'}，B 用了 {sorted(only_b_props) or '∅'}"
        )
    only_a_verbs = a.verbs - b.verbs
    only_b_verbs = b.verbs - a.verbs
    if only_a_verbs or only_b_verbs:
        conflicts.append(
            f"动作差异：A 含 {sorted(only_a_verbs) or '∅'}，B 含 {sorted(only_b_verbs) or '∅'}"
        )
    return tuple(conflicts)


def detect_intra_chapter_stitched_drafts(
    chapter_text: str,
    *,
    similarity_threshold: float = 0.62,
    min_participants_overlap: int = 2,
    min_props_overlap: int = 1,
    name_pool: frozenset[str] | None = None,
    length_ratio_range: tuple[float, float] = (0.6, 1.7),
) -> list[StitchedDraftFinding]:
    """Detect "two drafts stitched together" inside one chapter.

    Args:
        chapter_text: full chapter markdown.
        similarity_threshold: weighted-jaccard cutoff above which two blocks
            are considered the same event re-told.
        min_participants_overlap: minimum shared named characters required
            for a candidate pair (filters out coincidence).
        min_props_overlap: minimum shared characteristic prop required
            (e.g. both blocks mention "暗格" or "玉简").
        name_pool: optional explicit set of canonical character names. When
            provided (typically from
            :func:`character_alias_canon.load_character_canon`), this is used
            verbatim as the participant pool, bypassing the frequency-based
            heuristic. Strongly recommended in production -- the heuristic
            pool is polluted by prose tokens which inflates the union set in
            Jaccard scoring and depresses similarities below the threshold.

    Returns a list of ``StitchedDraftFinding``; empty if the chapter is clean.
    Cost: O(N^2) over event blocks but N is typically <= 20 per chapter.
    """
    blocks = _split_into_event_blocks(chapter_text)
    pool = name_pool if name_pool is not None else frozenset(_extract_chapter_name_pool(chapter_text))
    sigs = [_signature_of(i, off, txt, pool) for i, (off, txt) in enumerate(blocks)]
    block_lengths = [len(txt.replace("\n", "")) for _, txt in blocks]
    lo, hi = length_ratio_range

    findings: list[StitchedDraftFinding] = []
    for i in range(len(sigs)):
        for j in range(i + 1, len(sigs)):
            a, b = sigs[i], sigs[j]
            # Cheap prefilter
            if len(a.participants & b.participants) < min_participants_overlap:
                continue
            if len(a.props & b.props) < min_props_overlap:
                continue
            # Stitched drafts are different prose RENDITIONS of the same scene
            # and therefore tend to have similar lengths. Two blocks with very
            # different lengths sharing many participants/props are almost
            # always genuinely different scenes that happen to reuse plot props
            # (令牌 / 灵镜 / 暗格 recurring across an arc) -- not stitched.
            if block_lengths[j] == 0:
                continue
            ratio = block_lengths[i] / block_lengths[j]
            if not (lo <= ratio <= hi):
                continue
            sim = _event_similarity(a, b)
            if sim >= similarity_threshold:
                findings.append(
                    StitchedDraftFinding(
                        block_a=a,
                        block_b=b,
                        similarity=round(sim, 3),
                        conflicts=_list_conflicts(a, b),
                    )
                )
    return findings


def build_stitched_draft_repair_prompt(
    findings: list[StitchedDraftFinding],
) -> str:
    """Render an editor-facing instruction listing the colliding blocks.

    Editor's job is to **pick one version and delete the other** -- never to
    merge or rewrite both. Merging tends to keep contradictory props from
    both drafts (e.g. "册子 AND 玉简") which is the bug we are trying to fix.
    """
    if not findings:
        return ""

    bullets: list[str] = []
    for f in findings:
        bullets.append(
            f"- 段落 #{f.block_a.block_index} 与 #{f.block_b.block_index} "
            f"事件签名相似度 {f.similarity}，疑似同一事件的两版草稿被同时保留。\n"
            f"  · 段 A 起头：{f.block_a.excerpt}\n"
            f"  · 段 B 起头：{f.block_b.excerpt}\n"
            f"  · 共同参与者：{sorted(f.block_a.participants & f.block_b.participants)}\n"
            f"  · 冲突项：{'; '.join(f.conflicts) if f.conflicts else '无显式冲突，但事件结构高度重叠'}"
        )

    return (
        "【拼接稿修复任务】\n"
        "检测到本章存在 ≥1 对疑似「换皮重写」的段落（同事件、不同措辞）。\n"
        "请二选一保留，删除另一段；禁止合并两段（合并会留下道具/动作矛盾）。\n"
        "选择标准：留下与章节 outline 的 scene cards 一致、与前后章 canon 不冲突的版本。\n\n"
        + "\n".join(bullets)
    )
