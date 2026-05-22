"""Compose a ``ChapterSignalPack`` from a generated chapter text + context.

This is the boundary between "the writer just wrote chapter N" and "the
persona simulator scores chapter N". The builder runs deterministic
inspection (hook counts, payoff markers, pacing extraction, DNA drift
against the target) and lets the caller layer in optional critic scores
(novelty, consistency, prose quality) when those exist.

Design intent:
    * Pure, deterministic, no LLM in the hot path.
    * Tolerant of missing context — every external input is optional,
      defaults are neutral so the simulator never crashes mid-generation.
    * Single function the calling pipeline needs to know about. The
      tedious mapping from chapter text to evidence lives here, not in
      every workflow.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from bestseller.domain.market_constraint import ChapterMarketConstraints
from bestseller.domain.voice_dna import VoiceDNA
from bestseller.services.reader_persona_simulator import ChapterSignalPack
from bestseller.services.voice_signature import (
    compute_voice_dna_diff,
    extract_voice_dna_from_text,
)

logger = logging.getLogger(__name__)


_HOOK_MARKERS = (
    "？", "?", "！", "!",
    "却", "但", "然而", "忽然", "突然", "下一刻", "门外", "身后",
    "电话", "倒计时", "真相", "秘密", "名单", "令牌",
    "竟然", "居然", "原本", "他没想到", "她没想到",
)
_PAYOFF_MARKERS = (
    "终于", "果然", "原来", "之所以", "正是", "竟是", "应声",
    "破开", "兑现", "落定", "尘埃落定", "终于明白",
    "答案揭开", "谜底揭开", "豁然开朗",
)
_EMOTIONAL_MARKERS = (
    "心中", "心头", "心底", "胸口", "鼻尖", "眼眶",
    "颤抖", "战栗", "屏息", "凝视", "哽咽", "泣不成声",
    "怒火", "狂喜", "悲愤", "痛彻", "心如刀绞", "情难自禁",
    "笑", "哭", "怒", "悲",
)
_CLIFFHANGER_TAIL_MARKERS = (
    "下一刻", "话音未落", "便在此时", "就在此时", "未及",
    "却见", "竟看见", "竟出现", "话音方落",
    "门被推开", "破门而入",
    "—未完—", "未完", "（未完）",
)
_DIALOGUE_QUOTE_RE = re.compile(r'[“「『][^”」』]{2,}[”」』]')
_PARAGRAPH_SPLITTER = re.compile(r"\n+")


def build_signal_pack(
    chapter_text: str,
    *,
    chapter_position: int,
    target_voice_dna: VoiceDNA | None = None,
    constraints: ChapterMarketConstraints | None = None,
    novelty_score: float | None = None,
    consistency_score: float | None = None,
    prose_quality_score: float | None = None,
    extra_hook_markers: Sequence[str] = (),
    extra_payoff_markers: Sequence[str] = (),
) -> ChapterSignalPack:
    """Build a ChapterSignalPack from a generated chapter.

    Required: the chapter text and its 1-indexed position.

    Optional but strongly recommended:
        * ``target_voice_dna`` — without it ``voice_dna_drift`` defaults
          to 0 (treated as on-target).
        * ``constraints`` — without it market hook checks become no-op.
        * ``novelty_score`` / ``consistency_score`` / ``prose_quality_score``
          — caller-provided critic outputs in 0..1.
    """

    if chapter_position < 1:
        raise ValueError("chapter_position must be >= 1")

    text = chapter_text or ""
    n_chars = len(text)

    hook_count = _count_markers(text, _HOOK_MARKERS, extra_hook_markers)
    payoff_count = _count_markers(text, _PAYOFF_MARKERS, extra_payoff_markers)
    emotional_beat_count = _count_paragraph_hits(text, _EMOTIONAL_MARKERS)

    cliffhanger_strength = _cliffhanger_strength(text)

    if target_voice_dna is None:
        voice_drift = 0.0
        dialogue_ratio = action_ratio = interior_ratio = 0.0
    else:
        observed = extract_voice_dna_from_text(
            text, source_id=f"observed-ch-{chapter_position}"
        )
        diff = compute_voice_dna_diff(target_voice_dna, observed)
        voice_drift = diff.overall_drift
        dialogue_ratio = observed.pacing.dialogue_ratio
        action_ratio = observed.pacing.action_ratio
        interior_ratio = observed.pacing.interior_ratio

    target_length_min = 0
    target_length_max = 0
    market_hooks_required = 0
    market_hooks_hit = 0
    saturated_trope_hits = 0
    if constraints is not None:
        target_length_min = constraints.optimal_chapter_length_min
        target_length_max = constraints.optimal_chapter_length_max
        market_hooks_required = constraints.min_hooks_required
        market_hooks_hit = _count_substring_hits(text, constraints.must_hit_hooks)
        saturated_trope_hits = _count_substring_hits(
            text, constraints.saturated_tropes
        )

    novelty = _bounded(novelty_score, default=0.55)
    consistency = _bounded(consistency_score, default=0.75)
    prose = _bounded(prose_quality_score, default=0.65)

    return ChapterSignalPack(
        chapter_position=chapter_position,
        chapter_text_chars=n_chars,
        hook_count=hook_count,
        payoff_count=payoff_count,
        cliffhanger_strength=cliffhanger_strength,
        voice_dna_drift=voice_drift,
        market_hooks_hit=market_hooks_hit,
        market_hooks_required=market_hooks_required,
        novelty_score=novelty,
        consistency_score=consistency,
        emotional_beat_count=emotional_beat_count,
        saturated_trope_hits=saturated_trope_hits,
        target_length_min=target_length_min,
        target_length_max=target_length_max,
        dialogue_ratio=dialogue_ratio,
        action_ratio=action_ratio,
        interior_ratio=interior_ratio,
        prose_quality_score=prose,
    )


# ---------- internals ----------


def _count_markers(
    text: str, markers: Sequence[str], extras: Sequence[str] = ()
) -> int:
    if not text:
        return 0
    total = 0
    for marker in (*markers, *extras):
        if not marker:
            continue
        total += text.count(marker)
    return total


def _count_substring_hits(text: str, items: Sequence[str]) -> int:
    if not text:
        return 0
    hits = 0
    for item in items:
        if not item:
            continue
        # Strip "avoid_saturated:" prefix used by market constraint compiler.
        needle = item.split(":", 1)[1] if item.startswith("avoid_saturated:") else item
        if needle and needle in text:
            hits += 1
    return hits


def _count_paragraph_hits(text: str, markers: Sequence[str]) -> int:
    if not text:
        return 0
    paragraphs = [p for p in _PARAGRAPH_SPLITTER.split(text) if p.strip()]
    hits = 0
    for paragraph in paragraphs:
        if any(m in paragraph for m in markers):
            hits += 1
    return hits


def _cliffhanger_strength(text: str) -> float:
    if not text:
        return 0.0
    tail = text[-400:]
    marker_hits = sum(1 for m in _CLIFFHANGER_TAIL_MARKERS if m in tail)
    punctuation_hits = sum(tail.count(p) for p in ("？", "!", "！", "…"))
    raw = marker_hits * 0.25 + min(punctuation_hits, 4) * 0.1
    return max(0.0, min(1.0, raw))


def _bounded(value: float | None, *, default: float) -> float:
    if value is None:
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


__all__ = ["build_signal_pack"]
