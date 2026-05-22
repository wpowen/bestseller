"""Voice Signature DNA — offline extraction + prompt-side rendering.

Pipeline:
    raw text (one or many reference books)
        -> ``extract_voice_dna_from_text(text, source_id)``
        -> ``VoiceDNA``
    multiple ``VoiceDNA`` samples
        -> ``blend_voice_dna(...)``
        -> a single fused target ``VoiceDNA``

The fused DNA is then rendered via ``render_voice_dna_block`` and
injected into chapter prompts. After each generated chapter, the same
extractor runs on the new chapter to produce a "live" DNA which is
compared against the target via ``compute_voice_dna_diff``.

All extraction is **deterministic** (no LLM dependency) so repeated calls
on the same text always produce identical output and tests stay cheap.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from statistics import mean, median, pstdev
from typing import Any

from bestseller.domain.voice_dna import (
    PacingSignature,
    RhetoricSignals,
    SentenceLengthHistogram,
    VoiceDNA,
    VoiceDNADiff,
)

logger = logging.getLogger(__name__)


_SENTENCE_TERMINATORS = "。！？!?…"
_PARAGRAPH_SPLITTER = re.compile(r"\n\s*\n+")
_SENTENCE_SPLITTER = re.compile(r"(?<=[" + _SENTENCE_TERMINATORS + r"])\s*")
_DIALOGUE_CHAR_RE = re.compile(r'["“”「」『』]')
_DIALOGUE_LINE_RE = re.compile(r'[“「『][^”」』]{2,}[”」』]')
_ACTION_VERBS = (
    "冲", "扑", "挥", "砸", "刺", "踢", "踹", "撞", "拔", "斩", "劈",
    "抓", "扯", "甩", "扔", "推", "拽", "踩", "跃", "扑", "扣", "扫",
    "落下", "腾起", "扑出", "拔剑", "出剑", "出拳", "握紧",
)
_INTERIOR_MARKERS = (
    "心中", "心里", "暗想", "暗道", "心念", "心头", "脑海", "脑子里",
    "意识到", "明白了", "想起", "想到", "回忆", "记忆", "心生", "感到",
)
_DESCRIPTION_MARKERS = (
    "阳光", "月光", "晨雾", "夜色", "风声", "山色", "云海", "灯光",
    "天空", "石壁", "屋檐", "街道", "院子", "宫殿", "殿宇", "湖面",
    "炊烟", "炉火",
)

_SIMILE_RE = re.compile(r"[像如似仿佛犹如][^。！？\n]{1,30}?[一般似的]?[。，！？\n]")
_PARALLELISM_RE = re.compile(r"([^，。！？\n]{2,8}[，；])\1{1,}")
_RHETORICAL_Q_RE = re.compile(r"[难岂何怎][^。！？\n]{1,30}?\?|[^\n]{1,30}?[难岂何怎][^\n]{1,30}[？]")
_ELLIPSIS_RE = re.compile(r"…{1,}|\.\.\.+|。{3,}")
_EXCLAMATION_RE = re.compile(r"[!！]")
_INTERJECTION_RE = re.compile(r"[啊哎呃哼嗯唉嘿哟哇]+[，。！？]")

_CLASSICAL_MARKERS = frozenset(
    "之乎者也矣焉哉夫盖凡岂尔斯兹此其乃则曰云".replace("\n", "")
)

_PUNCT_RE = re.compile(r"[　-〿＀-￯\s\d\W]")
_CJK_RE = re.compile(r"[一-鿿]")
# Heuristic threshold within the main CJK Unified Ideographs block (0x4E00..0x9FFF).
# Codepoint ordering is NOT strictly frequency-ordered, but in practice the upper
# quintile (>= 0x9000) is enriched for less-common characters. Anything *outside*
# the main block (Extension A/B/C/D/E) is treated as definitively rare.
_MAIN_CJK_RARE_THRESHOLD = 0x9000

_PHRASE_MIN_LEN = 3
_PHRASE_MAX_LEN = 6
_PHRASE_MIN_OCCURRENCES = 5
_TOP_CATCHPHRASES = 15
_TOP_OPENERS = 8
_TOP_CLOSERS = 8


def extract_voice_dna_from_text(
    text: str,
    *,
    source_id: str,
    source_label: str = "",
    taboo_phrases: Iterable[str] | None = None,
    register_hint: str = "",
    excluded_phrases: Iterable[str] | None = None,
) -> VoiceDNA:
    """Extract a deterministic VoiceDNA from raw text.

    The text should already be normalized (no BOM, consistent line endings).
    This function is pure and idempotent.
    """

    normalized = _normalize_text(text)
    if not normalized.strip():
        return _empty_dna(
            source_id=source_id, source_label=source_label, style_register=register_hint
        )

    sentences = _split_sentences(normalized)
    paragraphs = _split_paragraphs(normalized)

    sentence_length = _sentence_length_histogram(sentences)
    rhetoric = _rhetoric_signals(normalized)
    pacing = _pacing_signature(paragraphs, normalized)

    rare_density = _rare_char_density(normalized)
    classical_density = _classical_marker_density(normalized)

    excluded = {phrase.strip() for phrase in (excluded_phrases or []) if phrase}
    catchphrases, openers, closers = _extract_phrase_signatures(
        sentences, excluded=excluded
    )

    confidence = _confidence_for_sample(len(normalized))
    notes = []
    if len(normalized) < 2000:
        notes.append("sample too short — DNA confidence reduced")

    return VoiceDNA(
        source_id=source_id,
        source_label=source_label or source_id,
        sample_chars=len(normalized),
        sentence_length=sentence_length,
        rhetoric=rhetoric,
        pacing=pacing,
        rare_char_density=rare_density,
        classical_marker_density=classical_density,
        catchphrases=catchphrases,
        favorite_openers=openers,
        favorite_closers=closers,
        taboo_phrases=list(taboo_phrases or []),
        style_register=register_hint,
        confidence=confidence,
        notes=notes,
    )


def blend_voice_dna(
    samples: Sequence[VoiceDNA],
    *,
    weights: Sequence[float] | None = None,
    blended_source_id: str = "blended",
    blended_label: str = "",
) -> VoiceDNA:
    """Blend multiple DNAs into one weighted-average target profile.

    Catchphrases, openers, closers, and taboo phrases are merged (intersection +
    union) — items appearing in multiple samples gain priority, items unique to
    a single sample fall to the tail. This favors stable signatures over
    sample-specific noise.
    """

    if not samples:
        raise ValueError("blend_voice_dna requires at least one sample")

    if weights is None:
        weights = [1.0] * len(samples)
    if len(weights) != len(samples):
        raise ValueError("weights length must match samples length")

    total_weight = sum(weights)
    if total_weight <= 0:
        raise ValueError("total weight must be positive")

    def _wavg(values: Sequence[float]) -> float:
        return sum(v * w for v, w in zip(values, weights, strict=True)) / total_weight

    s_lens = [s.sentence_length for s in samples]
    sentence_length = SentenceLengthHistogram(
        p10=_wavg([s.p10 for s in s_lens]),
        p25=_wavg([s.p25 for s in s_lens]),
        p50=_wavg([s.p50 for s in s_lens]),
        p75=_wavg([s.p75 for s in s_lens]),
        p90=_wavg([s.p90 for s in s_lens]),
        mean=_wavg([s.mean for s in s_lens]),
        stddev=_wavg([s.stddev for s in s_lens]),
        short_ratio=_wavg([s.short_ratio for s in s_lens]),
        long_ratio=_wavg([s.long_ratio for s in s_lens]),
    )

    rs = [s.rhetoric for s in samples]
    rhetoric = RhetoricSignals(
        simile_per_kchar=_wavg([r.simile_per_kchar for r in rs]),
        parallelism_per_kchar=_wavg([r.parallelism_per_kchar for r in rs]),
        rhetorical_question_per_kchar=_wavg([r.rhetorical_question_per_kchar for r in rs]),
        ellipsis_per_kchar=_wavg([r.ellipsis_per_kchar for r in rs]),
        exclamation_per_kchar=_wavg([r.exclamation_per_kchar for r in rs]),
        interjection_per_kchar=_wavg([r.interjection_per_kchar for r in rs]),
    )

    ps = [s.pacing for s in samples]
    pacing = PacingSignature(
        dialogue_ratio=_wavg([p.dialogue_ratio for p in ps]),
        action_ratio=_wavg([p.action_ratio for p in ps]),
        interior_ratio=_wavg([p.interior_ratio for p in ps]),
        description_ratio=_wavg([p.description_ratio for p in ps]),
        avg_paragraph_chars=_wavg([p.avg_paragraph_chars for p in ps]),
        avg_paragraphs_per_kchar=_wavg([p.avg_paragraphs_per_kchar for p in ps]),
    )

    catchphrases = _merge_phrase_lists(
        [s.catchphrases for s in samples], cap=_TOP_CATCHPHRASES
    )
    openers = _merge_phrase_lists([s.favorite_openers for s in samples], cap=_TOP_OPENERS)
    closers = _merge_phrase_lists([s.favorite_closers for s in samples], cap=_TOP_CLOSERS)
    taboos = _merge_phrase_lists([s.taboo_phrases for s in samples], cap=32)

    register_counts = Counter(s.style_register for s in samples if s.style_register)
    style_register = register_counts.most_common(1)[0][0] if register_counts else ""

    confidence = _wavg([s.confidence for s in samples])

    return VoiceDNA(
        source_id=blended_source_id,
        source_label=blended_label or f"blend({len(samples)})",
        sample_chars=sum(s.sample_chars for s in samples),
        sentence_length=sentence_length,
        rhetoric=rhetoric,
        pacing=pacing,
        rare_char_density=_wavg([s.rare_char_density for s in samples]),
        classical_marker_density=_wavg([s.classical_marker_density for s in samples]),
        catchphrases=catchphrases,
        favorite_openers=openers,
        favorite_closers=closers,
        taboo_phrases=taboos,
        style_register=style_register,
        confidence=confidence,
        notes=[],
    )


def compute_voice_dna_diff(target: VoiceDNA, observed: VoiceDNA) -> VoiceDNADiff:
    """Compare an observed sample's DNA against the target DNA."""

    sl = _relative_gap(observed.sentence_length.p50, target.sentence_length.p50)
    sl_short = abs(observed.sentence_length.short_ratio - target.sentence_length.short_ratio)
    sl_long = abs(observed.sentence_length.long_ratio - target.sentence_length.long_ratio)
    sentence_length_drift = _clamp01(0.5 * sl + 0.25 * sl_short + 0.25 * sl_long)

    rhetoric_drift = mean(
        [
            _relative_gap(observed.rhetoric.simile_per_kchar, target.rhetoric.simile_per_kchar),
            _relative_gap(
                observed.rhetoric.parallelism_per_kchar, target.rhetoric.parallelism_per_kchar
            ),
            _relative_gap(
                observed.rhetoric.rhetorical_question_per_kchar,
                target.rhetoric.rhetorical_question_per_kchar,
            ),
            _relative_gap(
                observed.rhetoric.ellipsis_per_kchar, target.rhetoric.ellipsis_per_kchar
            ),
            _relative_gap(
                observed.rhetoric.exclamation_per_kchar, target.rhetoric.exclamation_per_kchar
            ),
        ]
    )

    pacing_drift = mean(
        [
            abs(observed.pacing.dialogue_ratio - target.pacing.dialogue_ratio),
            abs(observed.pacing.action_ratio - target.pacing.action_ratio),
            abs(observed.pacing.interior_ratio - target.pacing.interior_ratio),
            abs(observed.pacing.description_ratio - target.pacing.description_ratio),
        ]
    )

    rare_drift = abs(observed.rare_char_density - target.rare_char_density)

    target_phrases = {p for p in target.catchphrases}
    observed_phrases = {p for p in observed.catchphrases}
    missing = sorted(target_phrases - observed_phrases)[:8]

    forbidden_hit: list[str] = []
    for forbidden in target.taboo_phrases:
        if forbidden and forbidden in observed.source_label:
            continue
        if forbidden and any(forbidden in cp for cp in observed.catchphrases):
            forbidden_hit.append(forbidden)

    overall = _clamp01(
        0.30 * sentence_length_drift
        + 0.25 * rhetoric_drift
        + 0.25 * pacing_drift
        + 0.20 * rare_drift
    )

    analysis_bits: list[str] = []
    if sentence_length_drift > 0.25:
        analysis_bits.append(
            f"句长漂移 {sentence_length_drift:.2f}（目标 p50={target.sentence_length.p50:.0f} "
            f"vs 实测 p50={observed.sentence_length.p50:.0f}）"
        )
    if rhetoric_drift > 0.25:
        analysis_bits.append(f"修辞密度漂移 {rhetoric_drift:.2f}")
    if pacing_drift > 0.20:
        analysis_bits.append(
            f"节奏分布漂移 {pacing_drift:.2f}（对话{observed.pacing.dialogue_ratio:.2f} / "
            f"动作{observed.pacing.action_ratio:.2f} / 内心{observed.pacing.interior_ratio:.2f}）"
        )
    if rare_drift > 0.05:
        analysis_bits.append(
            f"生僻字密度漂移 {rare_drift:.3f}（目标 {target.rare_char_density:.3f} vs "
            f"实测 {observed.rare_char_density:.3f}）"
        )
    if missing:
        analysis_bits.append(f"缺失招牌句式: {', '.join(missing[:5])}")
    if forbidden_hit:
        analysis_bits.append(f"踩到 taboo: {', '.join(forbidden_hit[:5])}")

    return VoiceDNADiff(
        overall_drift=overall,
        sentence_length_drift=sentence_length_drift,
        rhetoric_drift=_clamp01(rhetoric_drift),
        pacing_drift=_clamp01(pacing_drift),
        rare_char_drift=_clamp01(rare_drift),
        missing_catchphrases=missing,
        forbidden_phrases_hit=forbidden_hit,
        analysis="；".join(analysis_bits) if analysis_bits else "声纹一致",
    )


def render_voice_dna_block(
    dna: VoiceDNA | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
    max_phrases: int = 6,
) -> str:
    """Render a compact prompt block describing the target voice DNA."""

    payload = _to_payload(dna)
    if not payload:
        return ""

    label = str(payload.get("source_label") or payload.get("source_id") or "").strip()
    register = str(payload.get("register") or "").strip()
    confidence = payload.get("confidence")

    sl = payload.get("sentence_length") or {}
    if isinstance(sl, Mapping):
        median_len = sl.get("p50") or sl.get("median")
        short_ratio = sl.get("short_ratio")
        long_ratio = sl.get("long_ratio")
    else:
        median_len = short_ratio = long_ratio = None

    pacing = payload.get("pacing") or {}
    rhetoric = payload.get("rhetoric") or payload.get("rhetoric_per_kchar") or {}

    catchphrases = list(payload.get("catchphrases") or [])[:max_phrases]
    openers = list(payload.get("favorite_openers") or [])[:max_phrases]
    closers = list(payload.get("favorite_closers") or [])[:max_phrases]
    taboos = list(payload.get("taboo_phrases") or [])[:max_phrases]
    rare = payload.get("rare_char_density")
    classical = payload.get("classical_marker_density")

    if language.lower().startswith("zh"):
        lines = ["【作者声纹 DNA — 必须遵守】"]
        if label:
            lines.append(f"- 目标声纹: {label}")
        if register:
            lines.append(f"- 语体: {register}")
        if confidence is not None:
            lines.append(f"- 置信度: {confidence}")
        if median_len is not None:
            tail = []
            if short_ratio is not None:
                tail.append(f"短句占比≈{float(short_ratio):.2f}")
            if long_ratio is not None:
                tail.append(f"长句占比≈{float(long_ratio):.2f}")
            extra = f"（{'，'.join(tail)}）" if tail else ""
            lines.append(f"- 句长目标: 中位数≈{float(median_len):.0f}{extra}")
        if isinstance(pacing, Mapping):
            lines.append(
                "- 节奏配比: "
                f"对话{float(pacing.get('dialogue_ratio') or 0):.2f} / "
                f"动作{float(pacing.get('action_ratio') or 0):.2f} / "
                f"内心{float(pacing.get('interior_ratio') or 0):.2f} / "
                f"描写{float(pacing.get('description_ratio') or 0):.2f}"
            )
        if rare is not None:
            lines.append(f"- 生僻字密度: ≈{float(rare):.3f}")
        if classical is not None and float(classical) > 0:
            lines.append(f"- 文言/古体标记密度: ≈{float(classical):.3f}")
        if isinstance(rhetoric, Mapping) and rhetoric:
            r_bits = []
            for key, label_zh in (
                ("simile_per_kchar", "比喻"),
                ("parallelism_per_kchar", "排比"),
                ("rhetorical_question_per_kchar", "反问"),
                ("ellipsis_per_kchar", "省略"),
                ("exclamation_per_kchar", "感叹"),
            ):
                v = rhetoric.get(key)
                if v is not None:
                    r_bits.append(f"{label_zh}{float(v):.2f}")
            if r_bits:
                lines.append("- 修辞密度(每千字): " + " / ".join(r_bits))
        if catchphrases:
            lines.append("- 招牌句式/词汇: " + "; ".join(catchphrases))
        if openers:
            lines.append("- 偏好开头: " + "; ".join(openers))
        if closers:
            lines.append("- 偏好收尾: " + "; ".join(closers))
        if taboos:
            lines.append("- 禁用词/句式: " + "; ".join(taboos))
        lines.append(
            "- 写作时必须主动靠拢以上指标，避免回归到'平均网文'句长与节奏。"
        )
        return "\n".join(lines)

    lines = ["[Author Voice DNA — must comply]"]
    if label:
        lines.append(f"- Target voice: {label}")
    if register:
        lines.append(f"- Register: {register}")
    if median_len is not None:
        lines.append(f"- Sentence length median ≈ {float(median_len):.0f}")
    if catchphrases:
        lines.append("- Signature phrases: " + "; ".join(catchphrases))
    if taboos:
        lines.append("- Forbidden phrasings: " + "; ".join(taboos))
    lines.append("- Actively bend toward these targets; resist regression to the mean.")
    return "\n".join(lines)


# ---------- internals ----------


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for raw in re.split(r"\n+", text):
        raw = raw.strip()
        if not raw:
            continue
        parts = _SENTENCE_SPLITTER.split(raw)
        for part in parts:
            s = part.strip()
            if len(s) >= 2:
                out.append(s)
    return out


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = []
    for raw in _PARAGRAPH_SPLITTER.split(text):
        # tolerate single-newline paragraphs which are common in Chinese web fiction
        for line in raw.split("\n"):
            stripped = line.strip()
            if stripped:
                paragraphs.append(stripped)
    return paragraphs


def _sentence_length_histogram(sentences: Sequence[str]) -> SentenceLengthHistogram:
    if not sentences:
        return SentenceLengthHistogram(p10=0, p25=0, p50=0, p75=0, p90=0, mean=0, stddev=0)

    lengths = sorted(len(s) for s in sentences)

    def pct(p: float) -> float:
        if not lengths:
            return 0.0
        idx = max(0, min(len(lengths) - 1, int(round((p / 100.0) * (len(lengths) - 1)))))
        return float(lengths[idx])

    short_threshold = 12
    long_threshold = 40
    short_ratio = sum(1 for x in lengths if x <= short_threshold) / len(lengths)
    long_ratio = sum(1 for x in lengths if x >= long_threshold) / len(lengths)

    return SentenceLengthHistogram(
        p10=pct(10),
        p25=pct(25),
        p50=pct(50),
        p75=pct(75),
        p90=pct(90),
        mean=float(mean(lengths)),
        stddev=float(pstdev(lengths)) if len(lengths) > 1 else 0.0,
        short_ratio=short_ratio,
        long_ratio=long_ratio,
    )


def _rhetoric_signals(text: str) -> RhetoricSignals:
    kchar = max(1.0, len(text) / 1000.0)
    return RhetoricSignals(
        simile_per_kchar=len(_SIMILE_RE.findall(text)) / kchar,
        parallelism_per_kchar=len(_PARALLELISM_RE.findall(text)) / kchar,
        rhetorical_question_per_kchar=len(_RHETORICAL_Q_RE.findall(text)) / kchar,
        ellipsis_per_kchar=len(_ELLIPSIS_RE.findall(text)) / kchar,
        exclamation_per_kchar=len(_EXCLAMATION_RE.findall(text)) / kchar,
        interjection_per_kchar=len(_INTERJECTION_RE.findall(text)) / kchar,
    )


def _pacing_signature(paragraphs: Sequence[str], full_text: str) -> PacingSignature:
    if not paragraphs:
        return PacingSignature()

    total_chars = max(1, sum(len(p) for p in paragraphs))

    dialogue_chars = 0
    action_chars = 0
    interior_chars = 0
    description_chars = 0

    for p in paragraphs:
        n = len(p)
        is_dialogue = bool(_DIALOGUE_LINE_RE.search(p)) or sum(
            1 for ch in p if ch in '"“”「」『』'
        ) >= 2
        if is_dialogue:
            dialogue_chars += n
            continue
        if any(marker in p for marker in _INTERIOR_MARKERS):
            interior_chars += n
            continue
        if any(verb in p for verb in _ACTION_VERBS):
            action_chars += n
            continue
        if any(marker in p for marker in _DESCRIPTION_MARKERS):
            description_chars += n
            continue
        description_chars += n

    avg_paragraph_chars = total_chars / len(paragraphs)
    avg_per_k = len(paragraphs) / max(1.0, len(full_text) / 1000.0)

    return PacingSignature(
        dialogue_ratio=dialogue_chars / total_chars,
        action_ratio=action_chars / total_chars,
        interior_ratio=interior_chars / total_chars,
        description_ratio=description_chars / total_chars,
        avg_paragraph_chars=avg_paragraph_chars,
        avg_paragraphs_per_kchar=avg_per_k,
    )


def _rare_char_density(text: str) -> float:
    cjk_chars = [ch for ch in text if _CJK_RE.match(ch)]
    if not cjk_chars:
        return 0.0
    rare = sum(1 for ch in cjk_chars if _is_rare(ch))
    return rare / len(cjk_chars)


def _classical_marker_density(text: str) -> float:
    cjk_chars = [ch for ch in text if _CJK_RE.match(ch)]
    if not cjk_chars:
        return 0.0
    classical = sum(1 for ch in cjk_chars if ch in _CLASSICAL_MARKERS)
    return classical / len(cjk_chars)


def _is_rare(ch: str) -> bool:
    code = ord(ch)
    # Anything outside the main CJK Unified block (Extension A/B/C/D/E…) is
    # definitively a rare character — these almost never appear in mass-market
    # web fiction unless the author is reaching for archaic vocabulary.
    if not (0x4E00 <= code <= 0x9FFF):
        return True
    # Inside the main block, codepoints near the top of the range are biased
    # toward less common characters. This is a heuristic — for a precise
    # frequency-based filter the project should plug in a frequency table.
    return code >= _MAIN_CJK_RARE_THRESHOLD


def _extract_phrase_signatures(
    sentences: Sequence[str],
    *,
    excluded: set[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    if not sentences:
        return [], [], []

    excluded = excluded or set()

    def _is_excluded(phrase: str) -> bool:
        for blocked in excluded:
            if blocked and (blocked in phrase or phrase in blocked):
                return True
        return False

    phrase_counts: Counter[str] = Counter()
    for s in sentences:
        cleaned = _PUNCT_RE.sub("", s)
        if len(cleaned) < _PHRASE_MIN_LEN:
            continue
        for n in range(_PHRASE_MIN_LEN, min(len(cleaned), _PHRASE_MAX_LEN) + 1):
            for i in range(len(cleaned) - n + 1):
                ng = cleaned[i : i + n]
                if all(_CJK_RE.match(c) for c in ng):
                    phrase_counts[ng] += 1

    catchphrases = _filter_catchphrases(phrase_counts, is_excluded=_is_excluded)

    opener_counts: Counter[str] = Counter()
    closer_counts: Counter[str] = Counter()
    for s in sentences:
        head = _PUNCT_RE.sub("", s[:6])
        tail = _PUNCT_RE.sub("", s[-6:])
        if len(head) >= 2:
            opener_counts[head[:3]] += 1
        if len(tail) >= 2:
            closer_counts[tail[-3:]] += 1

    openers = [
        w
        for w, c in opener_counts.most_common(_TOP_OPENERS * 3)
        if c >= _PHRASE_MIN_OCCURRENCES and not _is_excluded(w)
    ][:_TOP_OPENERS]
    closers = [
        w
        for w, c in closer_counts.most_common(_TOP_CLOSERS * 3)
        if c >= _PHRASE_MIN_OCCURRENCES and not _is_excluded(w)
    ][:_TOP_CLOSERS]

    return catchphrases, openers, closers


def _filter_catchphrases(
    counts: Counter[str],
    *,
    is_excluded=None,
) -> list[str]:
    candidates = [
        (phrase, n)
        for phrase, n in counts.items()
        if n >= _PHRASE_MIN_OCCURRENCES
        and (is_excluded is None or not is_excluded(phrase))
    ]
    candidates.sort(key=lambda kv: (-kv[1] * len(kv[0]), kv[0]))
    selected: list[str] = []
    for phrase, _ in candidates:
        if any(phrase in existing or existing in phrase for existing in selected):
            continue
        selected.append(phrase)
        if len(selected) >= _TOP_CATCHPHRASES:
            break
    return selected


def _merge_phrase_lists(lists: Sequence[Sequence[str]], *, cap: int) -> list[str]:
    counter: Counter[str] = Counter()
    for lst in lists:
        for phrase in lst:
            if phrase:
                counter[phrase] += 1
    return [phrase for phrase, _ in counter.most_common(cap)]


def _confidence_for_sample(n_chars: int) -> float:
    if n_chars <= 0:
        return 0.0
    # diminishing-returns curve: ~0.5 at 2000 chars, ~0.85 at 50k, asymptote 1.0
    return float(min(1.0, 1.0 - math.exp(-n_chars / 30000.0)))


def _relative_gap(observed: float, target: float) -> float:
    denom = max(abs(target), 1e-6)
    return min(1.0, abs(observed - target) / denom)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _to_payload(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, VoiceDNA):
        return value.to_prompt_card()
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


def _empty_dna(*, source_id: str, source_label: str, style_register: str) -> VoiceDNA:
    return VoiceDNA(
        source_id=source_id,
        source_label=source_label or source_id,
        sample_chars=0,
        sentence_length=SentenceLengthHistogram(
            p10=0, p25=0, p50=0, p75=0, p90=0, mean=0, stddev=0
        ),
        rhetoric=RhetoricSignals(),
        pacing=PacingSignature(),
        rare_char_density=0.0,
        classical_marker_density=0.0,
        style_register=style_register,
        confidence=0.0,
        notes=["empty sample"],
    )


__all__ = [
    "extract_voice_dna_from_text",
    "blend_voice_dna",
    "compute_voice_dna_diff",
    "render_voice_dna_block",
]
