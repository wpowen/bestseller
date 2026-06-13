"""Gate-level safety: content-blind rhythm signals can't alone force repair.

``choppy_rhythm`` / ``staccato_saturation`` cannot reliably separate skilled
emotional fragments from 伪文学装腔, so a richly-fragmented *good* chapter
should never be pushed over the block threshold (which routes to
quality-degrading machine repair) on rhythm alone. Reflective/lexical signals
(epiphany over-reliance, not-X-but-Y, tier-1 clichés) remain fully blockable.
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import _score
from bestseller.services.ai_flavor.types import AiFlavorSpan


def _span(category: str, severity: str = "warn") -> AiFlavorSpan:
    return AiFlavorSpan(
        start=0, end=1, matched_text="x", rule_id="r", category=category,
        severity=severity, suggestions=(), sentence_span=(0, 1), why="",
    )


def test_many_rhythm_warns_stay_below_block_threshold() -> None:
    # 20 choppy warns would be 80 uncapped; capped they cannot reach 50.
    spans = tuple(_span("choppy_rhythm") for _ in range(20))
    assert _score(spans) < 50


def test_staccato_and_choppy_share_the_cap() -> None:
    spans = tuple(_span("choppy_rhythm") for _ in range(10)) + tuple(
        _span("staccato_saturation") for _ in range(10)
    )
    assert _score(spans) < 50


def test_reflective_signals_remain_blockable() -> None:
    # Genuine over-reliance (epiphany) is NOT capped and can still block.
    spans = tuple(_span("epiphany_announcement") for _ in range(13))
    assert _score(spans) >= 50


def test_rhythm_does_not_mask_a_real_reflective_block() -> None:
    # Rhythm capped at 24, but reflective warns push the total over anyway.
    spans = tuple(_span("choppy_rhythm") for _ in range(20)) + tuple(
        _span("negated_definition") for _ in range(10)
    )
    assert _score(spans) >= 50
