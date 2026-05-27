from __future__ import annotations

import pytest

from bestseller.services.chapter_batch_quality_gate import evaluate_chapter_batch_quality

pytestmark = pytest.mark.unit


def test_batch_quality_gate_flags_repeated_openings_and_endings() -> None:
    ending = "门后传来同一句低语，像有人贴着他们的耳朵重复昨晚的病历编号。"
    opening = "同一刻，雨水砸在旧医院门牌上，林渊看见门缝里伸出一张病历。"
    chapter_1 = f"# 第1章\n\n{opening}\n\n他推门进去。\n\n{ending}"
    chapter_2 = f"# 第2章\n\n{opening}\n\n她举枪靠近。\n\n{ending}"

    report = evaluate_chapter_batch_quality([(1, chapter_1), (2, chapter_2)])

    assert report is not None
    assert report.passed is False
    assert "CHAPTER_OPENING_REPETITION" in report.blocking_codes
    assert "ENDING_SENTENCE_WEAK" in report.blocking_codes


def test_batch_quality_gate_passes_varied_window() -> None:
    report = evaluate_chapter_batch_quality(
        [
            (1, "# 第1章\n\n井底响起三下敲击，林渊把镜片压进证物袋。\n\n雨声盖住了脚步。"),
            (2, "# 第2章\n\n冷柜灯忽然亮起，苏婉宁看见腕带上的新日期。\n\n门外有人转身离开。"),
        ]
    )

    assert report is not None
    assert report.passed is True
