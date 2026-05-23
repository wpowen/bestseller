from __future__ import annotations

import pytest

from bestseller.services.common_sense_gate import evaluate_common_sense_gate

pytestmark = pytest.mark.unit


def test_common_sense_gate_flags_unexplained_nosebleed() -> None:
    report = evaluate_common_sense_gate(
        "林渊把铜钱压到王建业额头，鼻血滴在对方脸上，没擦。",
        genre="灵异",
        sub_genre="民俗悬疑",
        chapter_number=1,
    )

    assert report.passed is False
    assert any(finding.code == "unexplained_body_state" for finding in report.findings)


def test_common_sense_gate_allows_supernatural_bleeding_with_visible_cost() -> None:
    report = evaluate_common_sense_gate(
        "铜钱反噬回来，林渊鼻血滴在掌心，血线顺着符纹爬向镜框。",
        genre="灵异",
        sub_genre="民俗悬疑",
        chapter_number=1,
    )

    assert report.passed is True


def test_common_sense_gate_flags_remaining_time_arithmetic_conflict() -> None:
    report = evaluate_common_sense_gate(
        "镜面上写着只剩半个小时。林渊骑电动车赶了二十分钟。"
        "他进门后看表，竟然还剩二十分钟。",
        genre="灵异",
        chapter_number=1,
    )

    assert report.passed is False
    assert any(
        finding.code == "remaining_time_arithmetic_conflict"
        for finding in report.findings
    )


def test_common_sense_gate_flags_countdown_scale_conflict() -> None:
    report = evaluate_common_sense_gate(
        "镜中女人说十五分钟。林渊看了眼手机，还剩两分钟。"
        "手机屏幕亮了。倒计时：11:44:07。子时。",
        genre="灵异",
        chapter_number=1,
    )

    assert report.passed is False
    assert any(finding.code == "countdown_scale_conflict" for finding in report.findings)


def test_common_sense_gate_flags_early_game_or_stitch_marker() -> None:
    report = evaluate_common_sense_gate(
        "外卖小哥被拖进墙里后，空气冻住了。【当前存活：6人。】",
        genre="灵异",
        chapter_number=2,
    )

    assert report.passed is False
    assert any(
        finding.code == "early_chapter_game_or_stitch_marker"
        for finding in report.findings
    )


def test_common_sense_gate_flags_unintroduced_mentor_reference() -> None:
    report = evaluate_common_sense_gate(
        "林渊蹲下身敲了三短一长。师父教的破门法。他掏出铜钱夹在指缝。",
        genre="灵异",
        chapter_number=1,
    )

    assert report.passed is False
    assert any(
        finding.code == "unintroduced_authority_reference"
        for finding in report.findings
    )


def test_common_sense_gate_flags_repeated_rescue_or_debt_beat() -> None:
    report = evaluate_common_sense_gate(
        "林渊把账印押上去，陈默的身体猛地往外一弹。"
        "他又说替小雨押一次，账印爬到小臂。"
        "白光炸开，陈默的身体被一股无形力量弹出门外。",
        genre="灵异",
        chapter_number=3,
    )

    assert report.passed is False
    assert any(
        finding.code == "repeated_rescue_or_debt_beat"
        for finding in report.findings
    )


def test_common_sense_gate_flags_early_character_crowding() -> None:
    report = evaluate_common_sense_gate(
        "小雨抱着膝盖。陈默盯着她。老道士开口。老张跪在镜子前。"
        "眼镜男生后退，女白领看着老太太，情侣缩在角落。",
        genre="灵异",
        chapter_number=2,
    )

    assert report.passed is False
    assert any(finding.code == "early_character_crowding" for finding in report.findings)


def test_common_sense_gate_flags_rule_term_onboarding_failure() -> None:
    report = evaluate_common_sense_gate(
        "认葬之后就是入账。否认者先入账，代认会变成替认。"
        "账主拿到回执，镜债转成血亲债，债主不能走。",
        genre="灵异",
        chapter_number=2,
    )

    assert report.passed is False
    assert any(
        finding.code == "rule_term_onboarding_failure"
        for finding in report.findings
    )


def test_common_sense_gate_allows_rule_term_with_onboarding() -> None:
    report = evaluate_common_sense_gate(
        "林渊说，认账不是承认自己有罪，而是承认这笔债跟自己有关。"
        "如果一个人否认，镜子就会先把他记入账页，这就是入账的代价。",
        genre="灵异",
        chapter_number=2,
    )

    assert not any(
        finding.code == "rule_term_onboarding_failure"
        for finding in report.findings
    )
