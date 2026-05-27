from __future__ import annotations

import pytest

from bestseller.services.chapter_splice_coherence_gate import (
    evaluate_chapter_splice_coherence,
)

pytestmark = pytest.mark.unit


def test_splice_gate_blocks_repeated_sentence() -> None:
    text = (
        "柜门在倒影里开了一条缝，里面伸出一只沾水的手。\n\n"
        "林渊把证物袋压低，先确认地上的水线没有断。\n\n"
        "柜门在倒影里开了一条缝，里面伸出一只沾水的手。"
    )

    report = evaluate_chapter_splice_coherence(text, chapter_number=8)

    assert report.verdict == "blocked"
    assert {finding.code for finding in report.findings} >= {
        "CHAPTER_SPLICE_REPEATED_SENTENCE"
    }


def test_splice_gate_blocks_presence_contradiction() -> None:
    text = (
        "苏婉宁早就走了，楼道里只剩林渊一个人的脚步声。\n"
        "林渊把碎片扣进证物袋，盯着电梯门上的灰。\n"
        "苏婉宁直起身，手里还捏着封条剩下的半截。"
    )

    report = evaluate_chapter_splice_coherence(text, chapter_number=5)

    assert report.verdict == "blocked"
    assert any(
        finding.code == "CHAPTER_SPLICE_PRESENCE_CONTRADICTION"
        for finding in report.findings
    )


def test_splice_gate_blocks_location_drift() -> None:
    text = (
        "十七栋楼道里的灯忽明忽暗，林渊把铜钱按在门缝上。\n"
        "苏婉宁让张建军留在十七栋，不要再碰电梯镜面。\n"
        "三点零八分，十一栋井口忽然传来同样的敲击声。"
    )

    report = evaluate_chapter_splice_coherence(text, chapter_number=10)

    assert report.verdict == "blocked"
    assert any(finding.code == "CHAPTER_SPLICE_LOCATION_DRIFT" for finding in report.findings)
