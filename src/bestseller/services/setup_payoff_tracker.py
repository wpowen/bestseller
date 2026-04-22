"""Phase 3: Setup → payoff debt tracker for humiliation/betrayal scenes.

Detects the classic 爽文 anti-pattern where the protagonist is
humiliated / wronged / underestimated but the counterattack or
face-slap payoff never arrives (or arrives too late). A chapter that
gets "setup" — i.e. contains humiliation-flavoured text — is expected
to be paid off by a ``COUNTERATTACK`` / ``FACE_SLAP`` /
``REVENGE_CLOSURE`` / ``UNDERDOG_WIN`` chapter within the next
``payoff_window_chapters``. Unpaid setups are emitted as debts so the
L7 audit layer can flag them (plan: ``PLEASURE_SETUP_PAYOFF_DEBT``).

This module is a pure-function analysis primitive, deliberately kept
independent of ``foreshadowing.py`` (which tracks *clue* payoff, a
different axis). The L7 ``SetupPayoffTrackerAudit`` wrapper in
``audit_loop.py`` is responsible for DB access; this file only
consumes plain chapter text + hype metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from bestseller.services.hype_engine import HypeType, classify_hype


# Default setup keywords — scene cues that a humiliation / wrongful-
# accusation / underestimation moment has landed on the protagonist.
# Kept deliberately conservative so the detector doesn't false-fire on
# antagonist-vs-antagonist banter; `humiliation_keywords` can be
# overridden per-project when a preset has its own vocabulary.
DEFAULT_HUMILIATION_KEYWORDS: tuple[str, ...] = (
    "羞辱",
    "嘲笑",
    "嘲讽",
    "讥讽",
    "讥笑",
    "冷笑",
    "蔑视",
    "轻蔑",
    "看不起",
    "瞧不起",
    "踩在脚下",
    "打脸",
    "耻笑",
    "冤枉",
    "诬陷",
    "栽赃",
    "背叛",
    "抛弃",
    "被捕",
    "被抓",
    "奚落",
    "鄙视",
    "狗眼看人低",
    "哄堂大笑",
)


# Hype types that count as a payoff for a prior humiliation setup.
# ``REVENGE_CLOSURE`` is included because a closure on an older debt
# still counts even if it lands during a new setup's window.
DEFAULT_PAYOFF_HYPE_TYPES: frozenset[HypeType] = frozenset(
    {
        HypeType.COUNTERATTACK,
        HypeType.FACE_SLAP,
        HypeType.REVENGE_CLOSURE,
        HypeType.UNDERDOG_WIN,
    }
)


DEFAULT_PAYOFF_WINDOW_CHAPTERS = 5


@dataclass(frozen=True)
class SetupEvent:
    """A chapter that looks like a humiliation / betrayal setup."""

    chapter_no: int
    matched_keywords: tuple[str, ...]


@dataclass(frozen=True)
class PayoffEvent:
    """A chapter whose hype type counts as a counterattack payoff."""

    chapter_no: int
    hype_type: HypeType
    source: str  # "persisted" | "classified"


@dataclass(frozen=True)
class SetupPayoffDebt:
    """A setup that went unpaid within its window.

    ``window_end_chapter`` is the last chapter that was eligible to
    settle the debt. Chapters after ``window_end_chapter`` that carry a
    payoff hype do not clear the debt — they are effectively a
    different beat.
    """

    setup_chapter: int
    window_end_chapter: int
    matched_keywords: tuple[str, ...]


@dataclass(frozen=True)
class SetupPayoffReport:
    setups: tuple[SetupEvent, ...]
    payoffs: tuple[PayoffEvent, ...]
    debts: tuple[SetupPayoffDebt, ...]
    payoff_window_chapters: int

    @property
    def debt_count(self) -> int:
        return len(self.debts)


def scan_humiliation_setups(
    *,
    chapter_texts: Sequence[tuple[int, str]],
    humiliation_keywords: Sequence[str] = DEFAULT_HUMILIATION_KEYWORDS,
) -> tuple[SetupEvent, ...]:
    """Find every chapter whose text hits at least one humiliation keyword.

    ``chapter_texts`` is a sequence of ``(chapter_no, text)`` pairs. The
    function is order-preserving and de-duplicates keyword hits per
    chapter so a keyword mentioned five times counts once.
    """

    if not chapter_texts:
        return ()
    active_keywords = tuple(kw for kw in humiliation_keywords if kw)
    if not active_keywords:
        return ()

    setups: list[SetupEvent] = []
    for chapter_no, text in chapter_texts:
        if not text:
            continue
        hits = tuple(kw for kw in active_keywords if kw in text)
        if hits:
            setups.append(
                SetupEvent(chapter_no=int(chapter_no), matched_keywords=hits)
            )
    return tuple(setups)


def identify_payoffs(
    *,
    chapter_hype: Sequence[tuple[int, HypeType | None]] = (),
    chapter_texts: Sequence[tuple[int, str]] = (),
    payoff_hype_types: frozenset[HypeType] = DEFAULT_PAYOFF_HYPE_TYPES,
    language: str = "zh-CN",
    classify_when_missing: bool = True,
) -> tuple[PayoffEvent, ...]:
    """Collect every chapter whose hype counts as a payoff.

    Pulls from two sources in precedence order:
      1. ``chapter_hype`` — explicit ``(chapter_no, HypeType)`` pairs
         persisted by the pipeline; treated as ground truth.
      2. ``chapter_texts`` — when ``classify_when_missing`` is True and
         a chapter is not in ``chapter_hype``, ``classify_hype`` runs
         on the text as a fallback.
    """

    explicit: dict[int, HypeType] = {
        int(ch): h for ch, h in chapter_hype if h is not None
    }

    combined: dict[int, tuple[HypeType, str]] = {
        ch: (h, "persisted") for ch, h in explicit.items()
    }
    if classify_when_missing and chapter_texts:
        for chapter_no, text in chapter_texts:
            ch = int(chapter_no)
            if ch in combined or not text:
                continue
            result = classify_hype(text, language)
            if result is not None:
                combined[ch] = (result[0], "classified")

    return tuple(
        PayoffEvent(chapter_no=ch, hype_type=h, source=source)
        for ch, (h, source) in sorted(combined.items())
        if h in payoff_hype_types
    )


def analyze_setup_payoff(
    *,
    chapter_texts: Sequence[tuple[int, str]],
    chapter_hype: Sequence[tuple[int, HypeType | None]] = (),
    humiliation_keywords: Sequence[str] = DEFAULT_HUMILIATION_KEYWORDS,
    payoff_hype_types: frozenset[HypeType] = DEFAULT_PAYOFF_HYPE_TYPES,
    payoff_window_chapters: int = DEFAULT_PAYOFF_WINDOW_CHAPTERS,
    language: str = "zh-CN",
    classify_when_missing: bool = True,
) -> SetupPayoffReport:
    """Primary entry point.

    For each detected setup chapter, check whether any payoff hype
    landed in chapters ``(setup + 1) .. (setup + payoff_window_chapters)``.
    If not, emit a ``SetupPayoffDebt`` finding.

    The check is skipped for setups whose window still extends past
    the last supplied chapter — the payoff may still legitimately
    arrive. This matters when the tracker runs mid-project.
    """

    setups = scan_humiliation_setups(
        chapter_texts=chapter_texts,
        humiliation_keywords=humiliation_keywords,
    )
    payoffs = identify_payoffs(
        chapter_hype=chapter_hype,
        chapter_texts=chapter_texts,
        payoff_hype_types=payoff_hype_types,
        language=language,
        classify_when_missing=classify_when_missing,
    )

    if payoff_window_chapters < 1:
        payoff_window_chapters = DEFAULT_PAYOFF_WINDOW_CHAPTERS

    payoff_chapters = {p.chapter_no for p in payoffs}
    last_chapter = max((int(ch) for ch, _ in chapter_texts), default=0)

    debts: list[SetupPayoffDebt] = []
    for setup in setups:
        window_end = setup.chapter_no + payoff_window_chapters
        if window_end > last_chapter:
            # Window not yet closed — leave it open for later chapters.
            continue
        paid = any(
            setup.chapter_no < ch <= window_end for ch in payoff_chapters
        )
        if not paid:
            debts.append(
                SetupPayoffDebt(
                    setup_chapter=setup.chapter_no,
                    window_end_chapter=window_end,
                    matched_keywords=setup.matched_keywords,
                )
            )

    return SetupPayoffReport(
        setups=setups,
        payoffs=payoffs,
        debts=tuple(debts),
        payoff_window_chapters=payoff_window_chapters,
    )


__all__ = [
    "DEFAULT_HUMILIATION_KEYWORDS",
    "DEFAULT_PAYOFF_HYPE_TYPES",
    "DEFAULT_PAYOFF_WINDOW_CHAPTERS",
    "PayoffEvent",
    "SetupEvent",
    "SetupPayoffDebt",
    "SetupPayoffReport",
    "analyze_setup_payoff",
    "identify_payoffs",
    "scan_humiliation_setups",
]
