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
