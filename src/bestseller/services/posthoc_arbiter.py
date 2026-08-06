"""Observe what a chapter actually did, then rule on the difference.

Why a third stance exists
-------------------------
Today a chapter that diverges from its plan is a *defect*: a gate fires and the
repair loop rewrites until the prose matches the plan or the budget runs out.
That framing has no way to express "the writer did something better than the
plan", so genuinely good divergence is spent money and churn.

The alternative, taken from the 笔枢 production line, splits the judgement in
two and moves the correction forward in time:

* **Observer** — purely deterministic. Lists differences between what the
  contract asked for and what the prose contains. Makes no judgement at all;
  that separation is what keeps the evidence trustworthy.
* **Arbiter** — rules on each difference: ``landed`` / ``missed`` /
  ``deviated`` / ``unplanned``. A *reasonable* deviation is adopted into canon
  rather than rewritten; a missed beat becomes an input to the next chapter's
  plan instead of a rewrite of this one.

Shadow mode
-----------
This module is **inert by default**. It writes a report and returns it; nothing
in the pipeline reads it yet. The plan is to accumulate rulings across several
books first, then use that data — rather than intuition — to decide which
content gates can safely be demoted. Turning it on with no evidence would just
be a new gate with a new false-positive profile, which is the exact cycle this
work is meant to break.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ArbiterMode",
    "BeatVerdict",
    "ChapterObservation",
    "PlannedBeat",
    "arbiter_mode",
    "observe_chapter",
]


class ArbiterMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"


def arbiter_mode() -> ArbiterMode:
    """Resolve the deployment policy. Defaults to ``off``.

    Off rather than shadow: the observer is cheap but not free, and a book run
    should not silently acquire new work because a module was merged.
    """

    raw = (os.getenv("BESTSELLER_POSTHOC_ARBITER_MODE", "") or "").strip().lower()
    try:
        return ArbiterMode(raw) if raw else ArbiterMode.OFF
    except ValueError:
        logger.warning(
            "unknown BESTSELLER_POSTHOC_ARBITER_MODE=%r; falling back to off", raw
        )
        return ArbiterMode.OFF


class BeatVerdict(str, Enum):
    """How a planned beat fared in the finished prose."""

    LANDED = "landed"
    MISSED = "missed"
    WEAK = "weak"
    """Some evidence, not enough to call it landed. Deliberately not a failure:
    the observer reports uncertainty instead of guessing."""


@dataclass(frozen=True)
class PlannedBeat:
    """One thing the contract asked this chapter to deliver."""

    field: str
    text: str


@dataclass(frozen=True)
class BeatObservation:
    beat: PlannedBeat
    verdict: BeatVerdict
    evidence_ratio: float
    evidence_excerpt: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "field": self.beat.field,
            "planned": self.beat.text[:200],
            "verdict": self.verdict.value,
            "evidence_ratio": round(self.evidence_ratio, 4),
            "evidence": self.evidence_excerpt[:120],
        }


@dataclass(frozen=True)
class ChapterObservation:
    """Deterministic diff between contract and prose. No judgement applied."""

    chapter_number: int
    beats: tuple[BeatObservation, ...] = ()
    unplanned_named_entities: tuple[str, ...] = ()
    prose_chars: int = 0

    @property
    def landed(self) -> tuple[BeatObservation, ...]:
        return tuple(b for b in self.beats if b.verdict is BeatVerdict.LANDED)

    @property
    def missed(self) -> tuple[BeatObservation, ...]:
        return tuple(b for b in self.beats if b.verdict is BeatVerdict.MISSED)

    @property
    def landing_rate(self) -> float | None:
        if not self.beats:
            return None
        return len(self.landed) / len(self.beats)

    def to_payload(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "prose_chars": self.prose_chars,
            "landing_rate": (
                round(self.landing_rate, 4) if self.landing_rate is not None else None
            ),
            "beats": [b.to_payload() for b in self.beats],
            "unplanned_named_entities": list(self.unplanned_named_entities),
        }


# Strip whitespace and punctuation (CJK and ASCII) so that formatting choices
# do not hide a beat that the prose actually delivered.
_PUNCT_CHARS = (
    "　，。！？、；：《》（）【】…—·「」『』“”‘’"
    "()[]{}<>\"'`,.!?;:-_~/\\|@#$%^&*+=\r\n\t "
)
_PUNCT = re.compile("[" + re.escape(_PUNCT_CHARS) + "]+")
_NGRAM = 4

#: Above this share of the beat's n-grams present in the prose, call it landed.
_LANDED_RATIO = 0.34
#: Below this, call it missed. Between the two is ``weak`` — reported as
#: uncertainty rather than resolved by guessing.
_MISSED_RATIO = 0.12


def _normalise(text: str | None) -> str:
    return _PUNCT.sub("", str(text or ""))


def _ngrams(text: str) -> set[str]:
    cleaned = _normalise(text)
    if len(cleaned) < _NGRAM:
        return set()
    return {cleaned[i : i + _NGRAM] for i in range(len(cleaned) - _NGRAM + 1)}


def _planned_beats(contract: Mapping[str, Any]) -> list[PlannedBeat]:
    beats: list[PlannedBeat] = []
    for name in ("chapter_goal", "main_conflict", "hook_description"):
        value = contract.get(name)
        if value and _normalise(value):
            beats.append(PlannedBeat(field=name, text=str(value)))
    revealed = contract.get("information_revealed")
    if isinstance(revealed, Sequence) and not isinstance(revealed, (str, bytes)):
        for index, item in enumerate(revealed):
            if item and _normalise(item):
                beats.append(
                    PlannedBeat(field=f"information_revealed[{index}]", text=str(item))
                )
    return beats


def observe_chapter(
    *,
    chapter_number: int,
    contract: Mapping[str, Any],
    prose: str,
) -> ChapterObservation:
    """Diff a chapter's contract against its prose. Deterministic, no LLM.

    Note what this deliberately does *not* do: decide whether a miss is
    acceptable, or whether a deviation was an improvement. Those are judgement
    calls and belong to the arbiter. Keeping the evidence-gathering free of
    judgement is what makes the evidence worth arbitrating over.
    """

    prose_grams = _ngrams(prose)
    observations: list[BeatObservation] = []

    for beat in _planned_beats(contract):
        beat_grams = _ngrams(beat.text)
        if not beat_grams or not prose_grams:
            continue
        ratio = len(beat_grams & prose_grams) / len(beat_grams)
        if ratio >= _LANDED_RATIO:
            verdict = BeatVerdict.LANDED
        elif ratio <= _MISSED_RATIO:
            verdict = BeatVerdict.MISSED
        else:
            verdict = BeatVerdict.WEAK
        observations.append(
            BeatObservation(
                beat=beat,
                verdict=verdict,
                evidence_ratio=ratio,
                evidence_excerpt=_first_shared_span(beat_grams, prose),
            )
        )

    return ChapterObservation(
        chapter_number=chapter_number,
        beats=tuple(observations),
        prose_chars=len(_normalise(prose)),
    )


def _first_shared_span(beat_grams: set[str], prose: str) -> str:
    """A short quotation showing where the beat surfaced, for auditability.

    Every ruling should be traceable to text a human can check — an unevidenced
    verdict is indistinguishable from a hallucinated one.
    """

    cleaned = _normalise(prose)
    for index in range(0, max(0, len(cleaned) - _NGRAM + 1)):
        if cleaned[index : index + _NGRAM] in beat_grams:
            start = max(0, index - 12)
            return cleaned[start : index + 28]
    return ""
