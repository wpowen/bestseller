"""Tests for the Phase 3 setup → payoff debt tracker.

Covers ``scan_humiliation_setups``, ``identify_payoffs``, and the
top-level ``analyze_setup_payoff`` entry point with a focus on:

  * Keyword detection (default + custom).
  * Window-based debt resolution (paid, unpaid, open).
  * Persisted-hype vs classifier-derived payoff sources.
  * Defensive handling of empty inputs and mid-project cutoffs.
"""

from __future__ import annotations

import pytest

from bestseller.services.hype_engine import HypeType
from bestseller.services.setup_payoff_tracker import (
    DEFAULT_HUMILIATION_KEYWORDS,
    DEFAULT_PAYOFF_HYPE_TYPES,
    DEFAULT_PAYOFF_WINDOW_CHAPTERS,
    PayoffEvent,
    SetupEvent,
    SetupPayoffDebt,
    SetupPayoffReport,
    analyze_setup_payoff,
    identify_payoffs,
    scan_humiliation_setups,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _texts(
    *pairs: tuple[int, str],
) -> tuple[tuple[int, str], ...]:
    """Tiny helper so tests read as chapter_no/text pairs."""
    return tuple(pairs)


# ---------------------------------------------------------------------------
# scan_humiliation_setups
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScanHumiliationSetups:
    def test_detects_single_humiliation_keyword(self) -> None:
        setups = scan_humiliation_setups(
            chapter_texts=_texts((1, "众人哄堂大笑，指着他不肯停。")),
        )
        assert len(setups) == 1
        assert setups[0].chapter_no == 1
        assert "哄堂大笑" in setups[0].matched_keywords

    def test_skips_chapters_without_keywords(self) -> None:
        setups = scan_humiliation_setups(
            chapter_texts=_texts(
                (1, "普通的一天，他在街上散步，看着远处的夕阳。"),
                (2, "没有任何冲突。"),
            ),
        )
        assert setups == ()

    def test_empty_text_is_ignored(self) -> None:
        setups = scan_humiliation_setups(
            chapter_texts=_texts((1, ""), (2, "   ")),
        )
        assert setups == ()

    def test_empty_chapter_list_returns_empty_tuple(self) -> None:
        assert scan_humiliation_setups(chapter_texts=()) == ()

    def test_multi_keyword_chapter_records_each_hit_once(self) -> None:
        text = "他被嘲讽，然后被嘲讽，又被冤枉。反复被嘲讽。"
        setups = scan_humiliation_setups(chapter_texts=_texts((1, text)))
        assert len(setups) == 1
        # Order follows the default keyword tuple, and each distinct
        # keyword appears exactly once.
        assert setups[0].matched_keywords.count("嘲讽") == 1
        assert "冤枉" in setups[0].matched_keywords

    def test_custom_keywords_override_defaults(self) -> None:
        # "叛徒" is not in DEFAULT_HUMILIATION_KEYWORDS.
        setups = scan_humiliation_setups(
            chapter_texts=_texts((1, "他被骂叛徒。")),
            humiliation_keywords=("叛徒",),
        )
        assert len(setups) == 1 and setups[0].chapter_no == 1

    def test_empty_keyword_list_returns_empty(self) -> None:
        setups = scan_humiliation_setups(
            chapter_texts=_texts((1, "随便一段话，被冤枉。")),
            humiliation_keywords=(),
        )
        assert setups == ()

    def test_order_preserved_by_input(self) -> None:
        setups = scan_humiliation_setups(
            chapter_texts=_texts(
                (3, "他被冤枉了一回。"),
                (1, "他被羞辱了一回。"),
                (2, "平静的一天。"),
            ),
        )
        # Preserves the caller's ordering (3, 1) not sorted.
        assert [s.chapter_no for s in setups] == [3, 1]


# ---------------------------------------------------------------------------
# identify_payoffs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdentifyPayoffs:
    def test_explicit_persisted_hype_is_picked_up(self) -> None:
        payoffs = identify_payoffs(
            chapter_hype=(
                (1, HypeType.COUNTERATTACK),
                (2, HypeType.FACE_SLAP),
                (3, HypeType.POWER_REVEAL),  # not a payoff type
            ),
        )
        chapters = {p.chapter_no for p in payoffs}
        assert chapters == {1, 2}
        for p in payoffs:
            assert p.source == "persisted"

    def test_classifier_fallback_fills_missing_chapters(self) -> None:
        # Chapter 1 has no persisted hype; chapter text hits COUNTERATTACK.
        payoffs = identify_payoffs(
            chapter_hype=(),
            chapter_texts=_texts(
                (1, "他以彼之道还击，反手一记反击，将对方反制。"),
                (2, "平静无事。"),
            ),
            classify_when_missing=True,
        )
        assert len(payoffs) == 1
        assert payoffs[0].chapter_no == 1
        assert payoffs[0].hype_type == HypeType.COUNTERATTACK
        assert payoffs[0].source == "classified"

    def test_classifier_is_skipped_when_flag_off(self) -> None:
        payoffs = identify_payoffs(
            chapter_hype=(),
            chapter_texts=_texts(
                (1, "他以彼之道还击，反手一记反击，将对方反制。"),
            ),
            classify_when_missing=False,
        )
        assert payoffs == ()

    def test_persisted_beats_classifier(self) -> None:
        # If both sources have data, persisted wins.
        payoffs = identify_payoffs(
            chapter_hype=((1, HypeType.FACE_SLAP),),
            chapter_texts=_texts(
                (1, "他以彼之道还击反击反制。"),  # would classify as COUNTERATTACK
            ),
            classify_when_missing=True,
        )
        assert len(payoffs) == 1
        assert payoffs[0].hype_type == HypeType.FACE_SLAP
        assert payoffs[0].source == "persisted"

    def test_non_payoff_hype_types_are_excluded(self) -> None:
        payoffs = identify_payoffs(
            chapter_hype=(
                (1, HypeType.POWER_REVEAL),
                (2, HypeType.CARESS_BY_FATE),
                (3, HypeType.DOMINATION),
            ),
        )
        assert payoffs == ()

    def test_none_hype_entries_are_ignored(self) -> None:
        payoffs = identify_payoffs(
            chapter_hype=(
                (1, None),
                (2, HypeType.COUNTERATTACK),
            ),
        )
        assert len(payoffs) == 1 and payoffs[0].chapter_no == 2

    def test_output_sorted_by_chapter_number(self) -> None:
        payoffs = identify_payoffs(
            chapter_hype=(
                (5, HypeType.COUNTERATTACK),
                (1, HypeType.FACE_SLAP),
                (3, HypeType.REVENGE_CLOSURE),
            ),
        )
        assert [p.chapter_no for p in payoffs] == [1, 3, 5]

    def test_custom_payoff_types_narrow_the_filter(self) -> None:
        payoffs = identify_payoffs(
            chapter_hype=(
                (1, HypeType.FACE_SLAP),
                (2, HypeType.COUNTERATTACK),
            ),
            payoff_hype_types=frozenset({HypeType.COUNTERATTACK}),
        )
        assert len(payoffs) == 1 and payoffs[0].chapter_no == 2


# ---------------------------------------------------------------------------
# analyze_setup_payoff
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnalyzeSetupPayoff:
    def test_paid_setup_produces_no_debt(self) -> None:
        report = analyze_setup_payoff(
            chapter_texts=_texts(
                (1, "他被嘲讽，众人冷笑。"),
                (2, "平静的一天。"),
                (3, "他以彼之道还击，反击成功。"),
                (4, "收尾场面。"),
                (5, "终章。"),
                (6, "终章之后。"),  # ensures window 1..6 fully closed
            ),
            payoff_window_chapters=5,
        )
        assert len(report.setups) == 1
        assert report.setups[0].chapter_no == 1
        assert report.debts == ()

    def test_unpaid_setup_produces_debt(self) -> None:
        report = analyze_setup_payoff(
            chapter_texts=_texts(
                (1, "他被嘲讽，众人冷笑。"),
                (2, "平静的一天。"),
                (3, "平静的一天。"),
                (4, "平静的一天。"),
                (5, "平静的一天。"),
                (6, "平静的一天。"),
                (7, "平静的一天。"),  # window 1..6 fully closed, no payoff
            ),
            payoff_window_chapters=5,
        )
        assert len(report.debts) == 1
        debt = report.debts[0]
        assert debt.setup_chapter == 1
        assert debt.window_end_chapter == 6
        assert "嘲讽" in debt.matched_keywords

    def test_open_window_does_not_emit_debt_yet(self) -> None:
        # The book currently stops at chapter 3. The window 1..6 is
        # still open — a payoff could legitimately land in chapters
        # 4-6. No debt should be emitted.
        report = analyze_setup_payoff(
            chapter_texts=_texts(
                (1, "他被嘲讽。"),
                (2, "平静。"),
                (3, "平静。"),
            ),
            payoff_window_chapters=5,
        )
        assert len(report.setups) == 1
        assert report.debts == ()

    def test_payoff_exactly_at_window_end_clears_debt(self) -> None:
        report = analyze_setup_payoff(
            chapter_texts=_texts(
                (1, "他被嘲讽。"),
                (2, "平静。"),
                (3, "平静。"),
                (4, "平静。"),
                (5, "平静。"),
            ),
            chapter_hype=((5, HypeType.COUNTERATTACK),),
            payoff_window_chapters=4,
        )
        # Window is 1..5 inclusive (setup + 4). Payoff at 5 clears it.
        assert report.debts == ()

    def test_payoff_just_past_window_does_not_clear(self) -> None:
        report = analyze_setup_payoff(
            chapter_texts=_texts(
                (1, "他被嘲讽。"),
                (2, "平静。"),
                (3, "平静。"),
                (4, "平静。"),
                (5, "平静。"),
                (6, "平静。"),
                (7, "以彼之道反击反制。"),  # too late
            ),
            payoff_window_chapters=5,
        )
        assert len(report.debts) == 1
        assert report.debts[0].setup_chapter == 1

    def test_payoff_in_same_chapter_does_not_self_clear(self) -> None:
        # Chapter 1 contains both humiliation and counterattack hints —
        # the setup is not allowed to self-resolve.
        report = analyze_setup_payoff(
            chapter_texts=_texts(
                (1, "他被嘲讽，但立刻以彼之道反击反制。"),
                (2, "平静。"),
                (3, "平静。"),
                (4, "平静。"),
                (5, "平静。"),
                (6, "平静。"),
                (7, "平静。"),
            ),
            payoff_window_chapters=5,
        )
        # Because payoff appears at chapter 1 (same as setup), window
        # starts at 2..6 — no payoff inside, debt stands.
        assert len(report.debts) == 1
        assert report.debts[0].setup_chapter == 1

    def test_multiple_setups_each_tracked_independently(self) -> None:
        report = analyze_setup_payoff(
            chapter_texts=_texts(
                (1, "他被嘲讽。"),
                (2, "平静。"),
                (3, "他被冤枉。"),
                (4, "平静。"),
                (5, "平静。"),
                (6, "平静。"),
                (7, "平静。"),
                (8, "平静。"),
                (9, "平静。"),
                (10, "平静。"),
            ),
            payoff_window_chapters=5,
        )
        # Both setups close their windows by chapter 10.
        # setup@1: window 1..6 — no payoff → debt
        # setup@3: window 3..8 — no payoff → debt
        assert len(report.debts) == 2
        assert {d.setup_chapter for d in report.debts} == {1, 3}

    def test_payoff_from_explicit_hype_is_preferred(self) -> None:
        # Chapter 2 has explicit FACE_SLAP persisted. Even if its text
        # would not classify as a payoff, the persisted value wins.
        report = analyze_setup_payoff(
            chapter_texts=_texts(
                (1, "他被嘲讽。"),
                (2, "无特定关键词的剧情。"),
                (3, "平静。"),
                (4, "平静。"),
                (5, "平静。"),
                (6, "平静。"),
                (7, "平静。"),
            ),
            chapter_hype=((2, HypeType.FACE_SLAP),),
            payoff_window_chapters=5,
        )
        assert report.debts == ()

    def test_custom_payoff_window_below_one_falls_back_to_default(self) -> None:
        report = analyze_setup_payoff(
            chapter_texts=_texts(
                (1, "他被嘲讽。"),
                (2, "平静。"),
                (3, "平静。"),
                (4, "平静。"),
                (5, "平静。"),
                (6, "平静。"),
                (7, "平静。"),
            ),
            payoff_window_chapters=0,
        )
        assert report.payoff_window_chapters == DEFAULT_PAYOFF_WINDOW_CHAPTERS

    def test_report_is_frozen_dataclass(self) -> None:
        report = analyze_setup_payoff(
            chapter_texts=_texts((1, "平凡的一天。")),
        )
        with pytest.raises(Exception):
            report.setups = ()  # type: ignore[misc]

    def test_empty_input_returns_empty_report(self) -> None:
        report = analyze_setup_payoff(chapter_texts=())
        assert report.setups == ()
        assert report.payoffs == ()
        assert report.debts == ()
        assert report.debt_count == 0

    def test_custom_humiliation_keywords_override(self) -> None:
        report = analyze_setup_payoff(
            chapter_texts=_texts(
                (1, "他被贴上叛徒的标签。"),
                (2, "平静。"),
                (3, "平静。"),
                (4, "平静。"),
                (5, "平静。"),
                (6, "平静。"),
                (7, "平静。"),
            ),
            humiliation_keywords=("叛徒",),
            payoff_window_chapters=5,
        )
        assert len(report.setups) == 1
        assert len(report.debts) == 1

    def test_debt_count_matches_debt_tuple_length(self) -> None:
        # Uses 嘲讽/冤枉 which are humiliation-only — keywords like
        # "羞辱" overlap with FACE_SLAP and would be reclassified as a
        # payoff by the text classifier, masking the second debt.
        report = analyze_setup_payoff(
            chapter_texts=_texts(
                (1, "他被嘲讽。"),
                (2, "他被冤枉。"),
                (3, "平静。"),
                (4, "平静。"),
                (5, "平静。"),
                (6, "平静。"),
                (7, "平静。"),
                (8, "平静。"),
                (9, "平静。"),
            ),
            payoff_window_chapters=5,
        )
        assert report.debt_count == len(report.debts)
        assert report.debt_count == 2


# ---------------------------------------------------------------------------
# Module surface checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleSurface:
    def test_default_humiliation_keywords_are_non_empty(self) -> None:
        assert len(DEFAULT_HUMILIATION_KEYWORDS) >= 10
        assert all(isinstance(k, str) and k for k in DEFAULT_HUMILIATION_KEYWORDS)

    def test_default_payoff_types_cover_core_four(self) -> None:
        assert HypeType.COUNTERATTACK in DEFAULT_PAYOFF_HYPE_TYPES
        assert HypeType.FACE_SLAP in DEFAULT_PAYOFF_HYPE_TYPES
        assert HypeType.REVENGE_CLOSURE in DEFAULT_PAYOFF_HYPE_TYPES
        assert HypeType.UNDERDOG_WIN in DEFAULT_PAYOFF_HYPE_TYPES

    def test_default_window_is_five(self) -> None:
        assert DEFAULT_PAYOFF_WINDOW_CHAPTERS == 5

    def test_dataclasses_are_frozen(self) -> None:
        ev = SetupEvent(chapter_no=1, matched_keywords=("x",))
        with pytest.raises(Exception):
            ev.chapter_no = 2  # type: ignore[misc]
        debt = SetupPayoffDebt(
            setup_chapter=1,
            window_end_chapter=6,
            matched_keywords=("x",),
        )
        with pytest.raises(Exception):
            debt.setup_chapter = 2  # type: ignore[misc]
        payoff = PayoffEvent(
            chapter_no=1,
            hype_type=HypeType.COUNTERATTACK,
            source="persisted",
        )
        with pytest.raises(Exception):
            payoff.chapter_no = 2  # type: ignore[misc]

    def test_report_debt_count_property(self) -> None:
        report = SetupPayoffReport(
            setups=(),
            payoffs=(),
            debts=(
                SetupPayoffDebt(
                    setup_chapter=1,
                    window_end_chapter=6,
                    matched_keywords=("冤枉",),
                ),
            ),
            payoff_window_chapters=5,
        )
        assert report.debt_count == 1
