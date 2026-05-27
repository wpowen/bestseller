from __future__ import annotations

from bestseller.services.outline_reveal_alignment_gate import (
    RevealScheduleItem,
    evaluate_outline_reveal_alignment,
)


def test_blocks_unknown_reveal_id() -> None:
    verdict = evaluate_outline_reveal_alignment(
        {"chapter_no": 9, "key_reveals": ["missing_id"]},
        reveal_schedule={},
    )

    assert verdict.verdict == "blocked"
    assert verdict.findings[0].code == "REVEAL_ID_UNKNOWN"


def test_blocks_too_early_reveal() -> None:
    verdict = evaluate_outline_reveal_alignment(
        {"chapter_no": 3, "key_reveals": ["kou_zhang_ren"]},
        reveal_schedule={
            "kou_zhang_ren": RevealScheduleItem(
                reveal_id="kou_zhang_ren",
                earliest_chapter=9,
            )
        },
    )

    assert verdict.verdict == "blocked"
    assert verdict.findings[0].code == "REVEAL_TOO_EARLY_IN_OUTLINE"


def test_passes_known_reveal_at_floor() -> None:
    verdict = evaluate_outline_reveal_alignment(
        {"chapter_no": 9, "key_reveals": ["kou_zhang_ren", "__no_reveal__"]},
        reveal_schedule={
            "kou_zhang_ren": RevealScheduleItem(
                reveal_id="kou_zhang_ren",
                earliest_chapter=9,
            )
        },
    )

    assert verdict.passed is True
