from __future__ import annotations

import pytest

from bestseller.services.hype_engine import HypeType
from bestseller.services.opening_three_function_gate import evaluate_opening_three_function

pytestmark = pytest.mark.unit


def test_opening_three_function_passes_when_three_chapters_have_distinct_jobs() -> None:
    report = evaluate_opening_three_function(
        chapter_texts=(
            (1, "林渊必须在子时前藏好阴阳眼，否则会暴露。他决定追查铜镜线索。"),
            (2, "他选择夜闯祠堂，失败的代价是妹妹被周老板扣下。"),
            (3, "铜镜改变了他的身份，他获得父亲旧名，也看见更大的幕后真相。"),
        ),
        chapter_hype=(
            (1, HypeType.COUNTERATTACK),
            (2, HypeType.FACE_SLAP),
            (3, HypeType.POWER_REVEAL),
        ),
    )

    assert report.passed is True
    assert report.issues == ()
    assert report.metrics["checked_chapters"] == [1, 2, 3]


def test_opening_three_function_reports_missing_jobs_and_repeated_stimulus() -> None:
    report = evaluate_opening_three_function(
        chapter_texts=(
            (1, "林渊醒来，街上很热闹，很多人都在说铜镜。"),
            (2, "林渊又看见很多人在谈铜镜，街上更热闹。"),
            (3, "林渊继续听人谈铜镜，场面非常热闹。"),
        ),
        chapter_hype=(
            (1, "noise"),
            (2, "noise"),
            (3, "noise"),
        ),
    )

    codes = {issue.id for issue in report.issues}

    assert report.passed is False
    assert "OPENING_CH1_PRESSURE_MISSING" in codes
    assert "OPENING_CH2_COST_PROOF_MISSING" in codes
    assert "OPENING_CH3_LONG_DESIRE_MISSING" in codes
    assert "OPENING_THREE_REPEATED_STIMULUS" in codes


def test_opening_three_function_empty_input_passes_with_missing_metrics() -> None:
    report = evaluate_opening_three_function()

    assert report.passed is True
    assert report.metrics["missing_chapters"] == [1, 2, 3]
