"""Chapter-level length stability gate.

The scene drafter already enforces a per-scene ±10% window through the
prompt, but chapter-level length can still drift badly:

* The scene-assemble step strips duplicate paragraphs without compensating
  for the lost words, so a 4-scene chapter that passes scene gates can
  emerge 800-1200 words under the ``words_per_chapter.min``.
* LLM ``max_tokens`` ceilings can silently truncate Chinese output before
  the final scene completes, producing a chapter that is 2500-3500 words
  short of target.
* The existing ``LengthEnvelopeCheck`` in :mod:`output_validator` only
  fires when ``ProjectInvariants.length_envelope`` is populated. Projects
  created before the invariants bootstrap have no such envelope, so the
  check returns empty and short chapters ship unchecked.

This module centralises the chapter-length policy so:

1. The thresholds always come from ``config.generation.words_per_chapter``
   (the real configured contract), not only from invariants.
2. The verdict carries severity bands (``warn_low / block_low / warn_high
   / block_high``) that downstream gates can filter.
3. The finding integrates with :mod:`write_safety_gate`, so a chapter that
   comes in 3000 words when the target is 6400 can hard-block a write in
   exactly the same way contradiction / identity / golden-three findings
   do today.

The module is pure — it does not read the database, it does not call the
LLM. It takes a word count + a word budget + optional thresholds and
returns an immutable report. All network / persistence decisions happen
upstream (``chapter_validator``, ``drafts.assemble_chapter_draft``,
``pipelines``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

__all__ = [
    "LengthStabilityBand",
    "LengthStabilityReport",
    "LENGTH_STABILITY_ISSUE_SEVERITY",
    "evaluate_chapter_length",
]


class LengthStabilityBand(str, Enum):
    """How far the measured word count strays from the target window.

    ``OK`` means the chapter landed inside ``[min, max]``. The four drift
    bands split into ``warn_*`` (inside the soft margin) and ``block_*``
    (outside the soft margin). Consumers decide which bands actually
    block.
    """

    OK = "OK"
    WARN_LOW = "WARN_LOW"
    BLOCK_LOW = "BLOCK_LOW"
    WARN_HIGH = "WARN_HIGH"
    BLOCK_HIGH = "BLOCK_HIGH"


# Severity mapping consumed by write_safety_gate.  ``block_*`` → major so
# the default ``blocked_severities=("critical", "major")`` config catches
# them; ``warn_*`` → minor so they only surface when explicitly requested.
LENGTH_STABILITY_ISSUE_SEVERITY: dict[str, str] = {
    LengthStabilityBand.BLOCK_LOW.value: "major",
    LengthStabilityBand.BLOCK_HIGH.value: "major",
    LengthStabilityBand.WARN_LOW.value: "minor",
    LengthStabilityBand.WARN_HIGH.value: "minor",
}


@dataclass(frozen=True)
class LengthStabilityReport:
    """Immutable verdict for a single chapter.

    Attributes mirror the inputs + the resolved band.  ``deviation_ratio``
    is positive for over-target and negative for under-target, expressed
    as a fraction of the target (e.g. ``-0.35`` == 35% short).
    """

    enabled: bool
    word_count: int
    min_words: int
    target_words: int
    max_words: int
    band: LengthStabilityBand
    deviation_ratio: float
    issue_code: str | None

    @property
    def is_blocking(self) -> bool:
        return self.band in (
            LengthStabilityBand.BLOCK_LOW,
            LengthStabilityBand.BLOCK_HIGH,
        )

    @property
    def is_warning(self) -> bool:
        return self.band in (
            LengthStabilityBand.WARN_LOW,
            LengthStabilityBand.WARN_HIGH,
        )


def evaluate_chapter_length(
    *,
    word_count: int,
    min_words: int,
    target_words: int,
    max_words: int,
    warn_margin: float = 0.10,
    enabled: bool = True,
) -> LengthStabilityReport:
    """Classify ``word_count`` against a ``[min, target, max]`` budget.

    ``warn_margin`` is the fraction of the window below/above the hard
    bounds that still counts as a soft warning rather than a block. E.g.
    ``warn_margin=0.10`` means a chapter that came in at ``0.9 * min`` is
    ``WARN_LOW``; below ``0.9 * min`` it becomes ``BLOCK_LOW``.

    When ``enabled`` is False the report still carries the measured word
    count but always reports ``OK`` with a ``None`` issue code, so
    downstream callers can uniformly consume the result.
    """
    if min_words < 0 or max_words < min_words:
        raise ValueError(
            "length_stability: min_words must be non-negative and "
            "max_words >= min_words"
        )
    if target_words < min_words or target_words > max_words:
        # Normalise the degenerate case — some tests / projects store a
        # target outside the [min,max] window; clamp rather than raise so
        # the gate keeps running.
        target_words = max(min_words, min(max_words, target_words))
    if warn_margin < 0:
        warn_margin = 0.0

    deviation_ratio = 0.0
    if target_words > 0:
        deviation_ratio = (word_count - target_words) / target_words

    if not enabled:
        return LengthStabilityReport(
            enabled=False,
            word_count=int(word_count),
            min_words=int(min_words),
            target_words=int(target_words),
            max_words=int(max_words),
            band=LengthStabilityBand.OK,
            deviation_ratio=deviation_ratio,
            issue_code=None,
        )

    low_soft = int(round(min_words * (1.0 - warn_margin)))
    high_soft = int(round(max_words * (1.0 + warn_margin)))

    if word_count < low_soft:
        band = LengthStabilityBand.BLOCK_LOW
        issue_code = "CHAPTER_LENGTH_BLOCK_LOW"
    elif word_count < min_words:
        band = LengthStabilityBand.WARN_LOW
        issue_code = "CHAPTER_LENGTH_WARN_LOW"
    elif word_count > high_soft:
        band = LengthStabilityBand.BLOCK_HIGH
        issue_code = "CHAPTER_LENGTH_BLOCK_HIGH"
    elif word_count > max_words:
        band = LengthStabilityBand.WARN_HIGH
        issue_code = "CHAPTER_LENGTH_WARN_HIGH"
    else:
        band = LengthStabilityBand.OK
        issue_code = None

    return LengthStabilityReport(
        enabled=True,
        word_count=int(word_count),
        min_words=int(min_words),
        target_words=int(target_words),
        max_words=int(max_words),
        band=band,
        deviation_ratio=deviation_ratio,
        issue_code=issue_code,
    )


def summarize_length_stability(reports: Iterable[LengthStabilityReport]) -> str:
    """Human-readable summary across a batch of chapters (debug / logs)."""
    counts: dict[str, int] = {}
    for report in reports:
        counts[report.band.value] = counts.get(report.band.value, 0) + 1
    if not counts:
        return "(no chapters)"
    parts = [f"{band}={n}" for band, n in sorted(counts.items())]
    return ", ".join(parts)
