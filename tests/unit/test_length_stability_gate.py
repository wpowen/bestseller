"""Unit tests for the chapter length stability gate (C5).

Covers :mod:`bestseller.services.length_stability_gate` in isolation and
its integration with :mod:`bestseller.services.write_safety_gate` so we
cover the end-to-end "short chapter → blocking finding" path that was
silent before this gate was wired in.
"""

from __future__ import annotations

import pytest

from bestseller.services.length_stability_gate import (
    LENGTH_STABILITY_ISSUE_SEVERITY,
    LengthStabilityBand,
    LengthStabilityReport,
    evaluate_chapter_length,
    summarize_length_stability,
)
from bestseller.services.write_safety_gate import (
    WriteSafetyBlockError,
    assert_no_write_safety_blocks,
    findings_from_length_stability_report,
)


pytestmark = pytest.mark.unit


# ── evaluate_chapter_length ────────────────────────────────────────────


def test_evaluate_chapter_length_ok_inside_window() -> None:
    report = evaluate_chapter_length(
        word_count=6400,
        min_words=5000,
        target_words=6400,
        max_words=7500,
    )
    assert report.enabled is True
    assert report.band is LengthStabilityBand.OK
    assert report.issue_code is None
    assert report.is_blocking is False
    assert report.is_warning is False


def test_evaluate_chapter_length_warn_low_between_soft_and_hard() -> None:
    # min=5000, warn_margin=0.10 → soft_low=4500.  wc=4800 is inside
    # [4500, 5000) → WARN_LOW.
    report = evaluate_chapter_length(
        word_count=4800,
        min_words=5000,
        target_words=6400,
        max_words=7500,
    )
    assert report.band is LengthStabilityBand.WARN_LOW
    assert report.issue_code == "CHAPTER_LENGTH_WARN_LOW"
    assert report.is_blocking is False
    assert report.is_warning is True


def test_evaluate_chapter_length_block_low_below_soft_margin() -> None:
    # wc=3500 is well below soft_low=4500 → BLOCK_LOW.
    report = evaluate_chapter_length(
        word_count=3500,
        min_words=5000,
        target_words=6400,
        max_words=7500,
    )
    assert report.band is LengthStabilityBand.BLOCK_LOW
    assert report.issue_code == "CHAPTER_LENGTH_BLOCK_LOW"
    assert report.is_blocking is True
    # deviation is negative ~= (3500-6400)/6400
    assert report.deviation_ratio < -0.4


def test_evaluate_chapter_length_warn_high_then_block_high() -> None:
    # max=7500, warn_margin=0.10 → soft_high=8250.  8100 > 7500 and
    # <= 8250 → WARN_HIGH; 9000 > 8250 → BLOCK_HIGH.
    warn_report = evaluate_chapter_length(
        word_count=8100,
        min_words=5000,
        target_words=6400,
        max_words=7500,
    )
    assert warn_report.band is LengthStabilityBand.WARN_HIGH
    assert warn_report.is_warning is True

    block_report = evaluate_chapter_length(
        word_count=9000,
        min_words=5000,
        target_words=6400,
        max_words=7500,
    )
    assert block_report.band is LengthStabilityBand.BLOCK_HIGH
    assert block_report.is_blocking is True
    assert block_report.deviation_ratio > 0.4


def test_evaluate_chapter_length_disabled_flag_returns_ok_band() -> None:
    report = evaluate_chapter_length(
        word_count=100,
        min_words=5000,
        target_words=6400,
        max_words=7500,
        enabled=False,
    )
    assert report.enabled is False
    assert report.band is LengthStabilityBand.OK
    assert report.issue_code is None


def test_evaluate_chapter_length_clamps_inverted_target() -> None:
    # Some projects store target=0 which is outside [min, max]. The gate
    # must clamp rather than blow up so callers get a usable report.
    report = evaluate_chapter_length(
        word_count=6000,
        min_words=5000,
        target_words=0,
        max_words=7500,
    )
    # Target was clamped to min_words=5000, so wc=6000 lands inside [5000, 7500].
    assert report.band is LengthStabilityBand.OK
    assert report.target_words == 5000


def test_evaluate_chapter_length_rejects_invalid_window() -> None:
    with pytest.raises(ValueError):
        evaluate_chapter_length(
            word_count=6000,
            min_words=5000,
            target_words=6400,
            max_words=4000,
        )


def test_evaluate_chapter_length_warn_margin_zero_promotes_to_block() -> None:
    # With warn_margin=0 the soft_low collapses onto min_words, so wc=4999
    # immediately becomes BLOCK_LOW instead of WARN_LOW.
    report = evaluate_chapter_length(
        word_count=4999,
        min_words=5000,
        target_words=6400,
        max_words=7500,
        warn_margin=0.0,
    )
    assert report.band is LengthStabilityBand.BLOCK_LOW


# ── summarize_length_stability ─────────────────────────────────────────


def test_summarize_length_stability_counts_bands() -> None:
    reports = [
        evaluate_chapter_length(
            word_count=6400, min_words=5000, target_words=6400, max_words=7500
        ),
        evaluate_chapter_length(
            word_count=3500, min_words=5000, target_words=6400, max_words=7500
        ),
        evaluate_chapter_length(
            word_count=4000, min_words=5000, target_words=6400, max_words=7500
        ),
    ]
    summary = summarize_length_stability(reports)
    # 1 OK + 2 BLOCK_LOW
    assert "OK=1" in summary
    assert "BLOCK_LOW=2" in summary


def test_summarize_length_stability_empty_iterable() -> None:
    assert summarize_length_stability([]) == "(no chapters)"


# ── write_safety_gate integration ─────────────────────────────────────


def test_findings_from_length_stability_block_low_surfaces_major() -> None:
    report = evaluate_chapter_length(
        word_count=3500,
        min_words=5000,
        target_words=6400,
        max_words=7500,
    )
    findings = findings_from_length_stability_report(
        report, blocked_severities=("major",)
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.source == "length_stability"
    assert finding.severity == "major"
    assert finding.code == "CHAPTER_LENGTH_BLOCK_LOW"
    assert finding.payload["word_count"] == 3500
    assert finding.payload["band"] == "BLOCK_LOW"
    assert "3500" in finding.message
    # Blocking finding must raise through assert_no_write_safety_blocks.
    with pytest.raises(WriteSafetyBlockError):
        assert_no_write_safety_blocks(
            findings,
            project_slug="my-story",
            chapter_number=1,
            scene_number=1,
        )


def test_findings_from_length_stability_warn_low_default_config_silent() -> None:
    # WARN_LOW is "minor" severity.  Default blocked_severities=("major",)
    # must NOT surface the finding — matches production default.
    report = evaluate_chapter_length(
        word_count=4800,
        min_words=5000,
        target_words=6400,
        max_words=7500,
    )
    assert LENGTH_STABILITY_ISSUE_SEVERITY["WARN_LOW"] == "minor"
    findings = findings_from_length_stability_report(
        report, blocked_severities=("major",)
    )
    assert findings == ()


def test_findings_from_length_stability_warn_low_when_minor_allowed() -> None:
    report = evaluate_chapter_length(
        word_count=4800,
        min_words=5000,
        target_words=6400,
        max_words=7500,
    )
    findings = findings_from_length_stability_report(
        report, blocked_severities=("major", "minor")
    )
    assert len(findings) == 1
    assert findings[0].severity == "minor"
    assert findings[0].code == "CHAPTER_LENGTH_WARN_LOW"


def test_findings_from_length_stability_disabled_returns_empty() -> None:
    disabled_report = evaluate_chapter_length(
        word_count=3500,
        min_words=5000,
        target_words=6400,
        max_words=7500,
        enabled=False,
    )
    # Even though the wc is technically below the hard floor, enabled=False
    # tells downstream code to suppress the finding entirely.
    findings = findings_from_length_stability_report(disabled_report)
    assert findings == ()


def test_findings_from_length_stability_flag_off_suppresses_block() -> None:
    report = evaluate_chapter_length(
        word_count=3500,
        min_words=5000,
        target_words=6400,
        max_words=7500,
    )
    findings = findings_from_length_stability_report(
        report, block_on_violation=False
    )
    assert findings == ()


def test_findings_from_length_stability_none_report_is_safe() -> None:
    assert findings_from_length_stability_report(None) == ()


def test_length_stability_report_dataclass_is_frozen() -> None:
    report = evaluate_chapter_length(
        word_count=6400,
        min_words=5000,
        target_words=6400,
        max_words=7500,
    )
    assert isinstance(report, LengthStabilityReport)
    with pytest.raises(Exception):
        # frozen dataclass — mutation must raise
        report.word_count = 0  # type: ignore[misc]
