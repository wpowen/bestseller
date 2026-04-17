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
) -> list[dict[str, Any]]:
    """Detect repeated paragraph blocks within a single assembled chapter.

    Parameters
    ----------
    chapter_text : str
        The fully assembled chapter markdown content.
    min_paragraph_length : int
        Minimum character length for a paragraph to be considered a duplicate
        candidate. Short paragraphs (single-sentence transitions) are ignored.

    Returns
    -------
    list of findings dicts with keys:
        first_pos  — index of first occurrence paragraph
        second_pos — index of duplicate paragraph
        text       — the repeated paragraph text (truncated to 120 chars)
        severity   — "critical" (exact match) or "major" (high-similarity)
        message    — human-readable description
    """
    paragraphs = _split_paragraphs(chapter_text)
    findings: list[dict[str, Any]] = []
    seen: dict[int, int] = {}  # normalized_hash → first paragraph index

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
                "severity": "critical",
                "message": (
                    f"[段落重复] 第{i+1}段与第{seen[key]+1}段内容完全相同，"
                    f"疑似场景拼接时产生重复。"
                ),
            })
        else:
            seen[key] = i

    return findings


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
