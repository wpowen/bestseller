from __future__ import annotations

import pytest

from bestseller.services.hype_engine import HypeType
from bestseller.services.reader_power import analyze_golden_three, serialize_golden_three_report

pytestmark = pytest.mark.unit


def test_golden_three_report_accepts_strong_opening_signals() -> None:
    report = analyze_golden_three(
        chapter_texts=(
            (1, "沈砚被羞辱后反击，血莲印忽然亮起，所有人当场沉默？"),
            (2, "敌人冷笑围住他，沈砚亮出底牌完成打脸，门外却传来追杀声！"),
            (3, "禁令压城，新的名单送到手中，真相只差最后一页。"),
        ),
        chapter_hype=(
            (1, HypeType.COUNTERATTACK),
            (2, HypeType.FACE_SLAP),
            (3, None),
        ),
        language="zh-CN",
    )

    payload = serialize_golden_three_report(report)

    assert payload["strong_hype_chapters"] >= 2
    assert payload["ending_hook_chapters"] >= 2
    assert "GOLDEN_THREE_LOW_HYPE" not in payload["issue_codes"]
    assert payload["chapters"][0]["assigned_hype_type"] == "counterattack"


def test_golden_three_report_flags_weak_opening() -> None:
    report = analyze_golden_three(
        chapter_texts=((1, "主角醒来，吃饭，出门，回家。"),),
        chapter_hype=(),
        language="zh-CN",
    )

    payload = serialize_golden_three_report(report)

    assert "GOLDEN_THREE_INCOMPLETE" in payload["issue_codes"]
    assert "GOLDEN_THREE_LOW_HYPE" in payload["issue_codes"]
    assert payload["chapters"][0]["issue_codes"]
