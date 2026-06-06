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


def test_common_sense_gate_allows_prior_chapter_bleeding_carryover() -> None:
    report = evaluate_common_sense_gate(
        "林渊的拇指还在往外渗血。铜钱边缘的缺口像一张咧开的嘴。",
        genre="灵异",
        sub_genre="民俗悬疑",
        chapter_number=2,
    )

    assert not any(finding.code == "unexplained_body_state" for finding in report.findings)


def test_common_sense_gate_ignores_blood_glyphs_as_body_bleeding() -> None:
    report = evaluate_common_sense_gate(
        "302门框上浮现出血字。笔画很粗，像是被人用手指蘸着血写上去。",
        genre="灵异",
        sub_genre="民俗悬疑",
        chapter_number=2,
    )

    assert not any(finding.code == "unexplained_body_state" for finding in report.findings)


def test_common_sense_gate_allows_accident_trauma_bleeding() -> None:
    # Regression (2026-06): a car-crash victim's bleeding is self-evidently
    # caused by the wreck. The gate's cause vocabulary was detective/xianxia
    # flavored (符/咒/反噬…) and lacked accident/trauma words, so an 都市 crash
    # scene tripped a false unexplained_body_state and blocked export.
    report = evaluate_common_sense_gate(
        "方向盘戳穿司机胸口，白色气囊上全是烟。半截指甲翻起来，血顺着指缝往下淌。"
        "车窗已经碎了。",
        genre="都市异能",
        sub_genre="身份反转",
        chapter_number=1,
    )

    assert not any(finding.code == "unexplained_body_state" for finding in report.findings)


def test_common_sense_gate_allows_urban_power_cost_bleeding() -> None:
    # The book's own cost mechanic (memory-pawn → nosebleed) uses 都市异能
    # vocabulary the detective token list did not cover (异能/能力/记忆/典当).
    report = evaluate_common_sense_gate(
        "他收走那段记忆的瞬间，异能的代价涌上来，鼻血毫无预兆地淌下来。",
        genre="都市异能",
        sub_genre="身份反转",
        chapter_number=1,
    )

    assert not any(finding.code == "unexplained_body_state" for finding in report.findings)


def test_common_sense_gate_ignores_supernatural_door_gap_bleeding() -> None:
    report = evaluate_common_sense_gate(
        "303的门虚掩着。林渊在门口停下。门缝里正往外渗血，不往下流，反而顺着门框往上爬。",
        genre="灵异",
        sub_genre="民俗悬疑",
        chapter_number=1,
    )

    assert not any(finding.code == "unexplained_body_state" for finding in report.findings)


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


def test_common_sense_gate_flags_impossible_body_action_sound() -> None:
    report = evaluate_common_sense_gate(
        "镜子里的张建军先点了点头的声音，随后才抬起脸。",
        genre="灵异",
        sub_genre="民俗悬疑",
        chapter_number=2,
    )

    assert report.passed is False
    assert any(
        finding.code == "impossible_body_action_sound"
        for finding in report.findings
    )


def test_common_sense_gate_allows_body_action_with_visible_sound_source() -> None:
    report = evaluate_common_sense_gate(
        "张建军点头时，钥匙磕在门锁上，发出很轻的声音。",
        genre="灵异",
        sub_genre="民俗悬疑",
        chapter_number=2,
    )

    assert not any(
        finding.code == "impossible_body_action_sound"
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


def test_common_sense_gate_flags_late_night_delivery_without_impossible_marker() -> None:
    report = evaluate_common_sense_gate(
        "张建军把配送单递过来，寄件时间写着23:58，王建业让他现在送旧镜。",
        genre="灵异",
        sub_genre="民俗悬疑",
        chapter_number=1,
    )

    assert report.passed is False
    assert any(finding.code == "late_night_delivery_plausibility" for finding in report.findings)


def test_common_sense_gate_flags_object_signal_overuse() -> None:
    report = evaluate_common_sense_gate(
        "铜钱发烫，林渊收回手。过了两步，铜钱又烫得像炭火。"
        "镜前风一吹，青囊账页也开始发烫。",
        genre="灵异",
        sub_genre="民俗悬疑",
        chapter_number=1,
    )

    assert report.passed is False
    assert any(finding.code == "object_signal_overuse" for finding in report.findings)


def test_common_sense_gate_flags_non_expert_rule_knowledge_leak() -> None:
    report = evaluate_common_sense_gate(
        "张建军堵在门口，脸色发白：“下一笔是不是我？我是不是已经入账了？”",
        genre="灵异",
        sub_genre="民俗悬疑",
        chapter_number=1,
    )

    assert report.passed is False
    assert any(
        finding.code == "lay_character_rule_knowledge_leak"
        for finding in report.findings
    )


def test_common_sense_gate_does_not_cross_paragraphs_for_rule_leak() -> None:
    report = evaluate_common_sense_gate(
        "林渊伸手挡在张建军身前，“别动。”\n\n"
        "张建军往后退了一步。\n\n"
        "林渊的心沉了一下。\n\n"
        "否认——不是死不认账那种否认。那是林渊自己的判断。",
        genre="灵异",
        sub_genre="民俗悬疑",
        chapter_number=2,
    )

    assert not any(
        finding.code == "lay_character_rule_knowledge_leak"
        for finding in report.findings
    )
