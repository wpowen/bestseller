from __future__ import annotations

import pytest

from bestseller.services.quality_closure import evaluate_quality_closure

pytestmark = pytest.mark.unit


def test_quality_closure_requires_current_blocking_codes_to_clear() -> None:
    report = evaluate_quality_closure(
        ["CHAPTER_OPENING_REPETITION", "ANTI_META_LEAK"],
        ["ANTI_META_LEAK"],
    )

    assert report.status == "open"
    assert report.resolved_codes == ("CHAPTER_OPENING_REPETITION",)
    assert report.remaining_blocking_codes == ("ANTI_META_LEAK",)


def test_quality_closure_reports_closed_after_all_previous_blocks_clear() -> None:
    report = evaluate_quality_closure(["CHAPTER_TOO_SHORT"], [])

    assert report.closed is True
    assert report.to_dict()["status"] == "closed"
