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


def test_presence_detector_ignores_object_marker_after_name() -> None:
    text = (
        "林渊把铜钱按在桌上。\n\n"
        "他没有离开，只是把目光转向镜面。\n\n"
        "林渊把证物袋推给苏婉宁。"
    )

    report = evaluate_chapter_splice_coherence(text, chapter_number=6)

    assert "CHAPTER_SPLICE_PRESENCE_CONTRADICTION" not in {
        finding.code for finding in report.findings
    }


def test_splice_gate_allows_countdown_and_deadline_time_anchors() -> None:
    text = (
        "凌晨三点，陆沉盯着申诉系统，屏幕右上角烧着倒计时：03:47:22。\n"
        "他把工单验证码敲进去，03:46:11，倒计时每一秒都在抽走余额。\n"
        "进度条爬到一半，03:45:58，03:45:57，提交按钮终于亮了。\n"
        "离明早八点审计归档还有四个多小时，他把回执压进证据袋。"
    )

    report = evaluate_chapter_splice_coherence(text, chapter_number=5)

    assert "CHAPTER_SPLICE_TIME_JUMP" not in {
        finding.code for finding in report.findings
    }


def test_splice_gate_ignores_non_time_point_phrases() -> None:
    text = (
        "陆沉账上只剩一点零头，杯里的水面有一点点震。\n"
        "屏幕边缘露出一点红光，键盘旁还有一点灰尘。\n"
        "他没有换地点，也没有切到另一条时间线，只把申诉材料继续往下填。"
    )

    report = evaluate_chapter_splice_coherence(text, chapter_number=1)

    assert "CHAPTER_SPLICE_TIME_JUMP" not in {
        finding.code for finding in report.findings
    }


def test_splice_gate_still_blocks_unbridged_cross_daypart_jumps() -> None:
    text = (
        "上午九点，林渊把证物袋按在桌角，登记簿还没有合上。\n"
        "中午十二点，尸检电话压进来，没人解释他怎么离开了证物室。\n"
        "下午三点，医院走廊突然亮起同一盏灯，原来的门锁不见了。\n"
        "深夜十一点，井口传来敲击声，前面的行动线全部断在原地。"
    )

    report = evaluate_chapter_splice_coherence(text, chapter_number=9)

    assert any(
        finding.code == "CHAPTER_SPLICE_TIME_JUMP"
        for finding in report.findings
    )


def test_splice_gate_allows_exit_continuation_after_leave_marker() -> None:
    text = (
        "庞琰转身往门外走。走廊尽头另一扇门推开，有人跟他并肩压低声音。"
        "庞琰脚步顿一下，没回头，往电梯方向走。电梯指示灯亮。"
        "陆沉把证据包链接复制进申诉材料备注栏。"
    )

    report = evaluate_chapter_splice_coherence(text, chapter_number=3)

    assert "CHAPTER_SPLICE_PRESENCE_CONTRADICTION" not in {
        finding.code for finding in report.findings
    }


def test_splice_gate_ignores_deadline_and_recalled_time_anchors() -> None:
    text = (
        "凌晨四点零三分，陆沉把上一时段工单锁进抽屉。"
        "他想起下午在工位屏幕上扫到的审计留痕提示。"
        "明早八点审计一过，共享字段就会被归档封存。"
        "截图里今早八点的待签名单还亮着。"
        "对讲机里说十五分钟内回电，现在只剩八分钟。"
    )

    report = evaluate_chapter_splice_coherence(text, chapter_number=4)

    assert "CHAPTER_SPLICE_TIME_JUMP" not in {
        finding.code for finding in report.findings
    }
