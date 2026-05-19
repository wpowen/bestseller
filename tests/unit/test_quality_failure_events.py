"""Unit tests for normalized quality failure events."""

from __future__ import annotations

import pytest

from bestseller.services.quality_failure_events import (
    failure_events_from_retrofit_row,
    quality_failure_event_from_dict,
)

pytestmark = pytest.mark.unit


def test_failure_events_from_retrofit_row_maps_detector_failures() -> None:
    row = {
        "slug": "book-a",
        "chapter_number": "3",
        "platform": "qimao",
        "language": "zh-CN",
        "priority": "high",
        "word_count_passed": "False",
        "word_count_reason": "underflow: 1400 < 2500",
        "char_count": "1400",
        "count_unit": "cjk_chars",
        "pulse_passed": "False",
        "pulse_density": "0.25",
        "pulse_threshold": "1.0",
        "pulse_count": "2",
        "rhythm_passed": "False",
        "rhythm_applicable": "True",
        "rhythm_types_covered": "2",
        "rhythm_expected_min_types": "3",
    }

    events = failure_events_from_retrofit_row(row, evidence_ref="audit.csv")

    assert [event.code for event in events] == [
        "WORD_COUNT_UNDERFLOW",
        "PULSE_DENSITY_BELOW_THRESHOLD",
        "RHYTHM_ANCHOR_COVERAGE_FAILED",
    ]
    assert events[0].slug == "book-a"
    assert events[0].chapter_number == 3
    assert events[0].severity == "high"
    assert events[0].platform == "qimao"
    assert events[0].evidence_ref == "audit.csv"
    assert events[0].details["count_unit"] == "cjk_chars"


def test_failure_events_skip_non_applicable_rhythm_gate() -> None:
    row = {
        "slug": "english-book",
        "chapter_number": "1",
        "platform": "tomato",
        "language": "en-US",
        "priority": "high",
        "rhythm_passed": "False",
        "rhythm_applicable": "False",
    }

    assert failure_events_from_retrofit_row(row) == ()


def test_invalid_audit_event_points_to_detector_remediation() -> None:
    row = {
        "slug": "english-book",
        "chapter_number": "1",
        "platform": "tomato",
        "language": "en-US",
        "priority": "high",
        "audit_validity": "invalid_audit_language_mismatch",
        "word_count_passed": "False",
        "word_count_reason": "underflow: 0 < 2000",
    }

    (event,) = failure_events_from_retrofit_row(row)

    assert event.source_stage == "detector"
    assert event.preventable_stage == "metadata_validation"
    assert event.remediation_class == "fix_detector_not_chapter"


def test_quality_failure_event_round_trip_from_dict() -> None:
    event = quality_failure_event_from_dict(
        {
            "slug": "book-a",
            "chapter_number": "2",
            "stage": "retrofit_audit",
            "gate_id": "quality_levers.word_count",
            "code": "WORD_COUNT_UNDERFLOW",
            "severity": "high",
            "language": "zh-CN",
            "platform": "qimao",
            "source_stage": "draft",
            "preventable_stage": "draft_generation",
            "remediation_class": "adjust_chapter_length",
            "details": {"char_count": "1200"},
        }
    )

    assert event.to_dict()["chapter_number"] == 2
    assert event.to_dict()["details"] == {"char_count": "1200"}
