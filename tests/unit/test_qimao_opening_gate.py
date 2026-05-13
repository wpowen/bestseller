from __future__ import annotations

import pytest

from bestseller.services.qimao_opening_gate import (
    evaluate_qimao_opening_gate,
    qimao_opening_gate_report_to_dict,
)

pytestmark = pytest.mark.unit


def test_qimao_opening_gate_passes_action_conflict_opening() -> None:
    chapters = {
        1: (
            "沈姝刚踏进祠堂，账本就被二叔按在火盆边。“签字，否则你娘的案子今晚就烧干净。”"
            "她一把扣住账页，指尖被火星烫出血，却顺势把藏在夹层里的旧印露给众人看。"
            "族老的脸色终于变了，逼她下跪的人反而退了半步。"
            "她夺回半页证据，还没来得及喘息，门外忽然响起母亲旧锁的声音：那把锁，怎么会在二叔手里？"
        ),
        2: (
            "沈姝抓着半页账本追到后巷，逼问送信人。对方拔刀，她侧身躲过，发现刀柄刻着母亲旧案的暗号。"
            "她反制成功，却也暴露了自己能读懂暗账的优势。巷口有人低声说，账本的另一半今晚会被送走？"
        ),
        3: (
            "沈姝拦下运账马车，用账页错位撬开第一层真相，拿到能逼二叔退让的筹码。"
            "她短暂翻盘，却发现筹码指向更高的人。若要救母亲，她必须今晚进府。"
        ),
    }

    report = evaluate_qimao_opening_gate(chapters, protagonist_name="沈姝")

    assert report.passed is True
    assert report.findings == ()


def test_qimao_opening_gate_uses_actual_long_chapter_ending_for_loop_hook() -> None:
    long_middle = (
        "苏砚沿着古宅墙根追查铜镜残痕，反复比对铭文和掌心旧疤。"
        "他按住镜框，血从指缝里渗出，仍然没有退开。"
    ) * 25
    chapter = (
        "苏砚刚踏进青萝镇，镇口铜钱就贴着树根震响。"
        "他抓住铜钱残痕追进古宅，镜中火光逼出母亲旧案的证据。"
        f"{long_middle}"
        "古宅外的雾气越来越浓，镜框右下角的灼痕亮起青光。"
        "他必须在那东西完全成形之前离开这里，但砚台还搁在条案上。"
        "身后，铜镜表面泛起一层淡淡的青光。"
        "那是铭纹鼎铭文被激活时才会呈现的颜色。"
    )

    report = evaluate_qimao_opening_gate({1: chapter}, protagonist_name="苏砚")

    assert report.passed is True
    assert report.findings == ()


def test_opening_gate_handles_english_conflict_opening() -> None:
    chapters = {
        1: (
            "Mara reached the vault as the officer shoved her father's ledger toward the fire. "
            "\"Sign, or the evidence dies with him.\" She grabbed the cover, burned her palm, "
            "and used the torn seal to force him back. She had the first proof, but then the lock "
            "on the dead man's room clicked behind her. Who still had that key?"
        ),
        2: (
            "Mara chased the courier into the alley, dodged the knife, and discovered "
            "the same seal inside his glove. She gained leverage, but exposed that she "
            "could read the hidden accounts."
        ),
        3: (
            "Mara blocked the carriage, recovered the missing page, and unlocked the "
            "first layer of proof. "
            "The win gave her leverage, but the evidence pointed to a higher patron."
        ),
    }

    report = evaluate_qimao_opening_gate(chapters, protagonist_name="Mara")

    assert report.passed is True
    assert report.findings == ()


def test_qimao_opening_gate_fails_background_worldbuilding_opening() -> None:
    text = (
        "天玄大陆有三千年历史，家族制度复杂，世界观设定分为内城与外城。"
        "多年以前，沈姝所在的沈家曾经掌握账房权力，家族由来可以追溯到前朝。"
        "这一切背景决定了她未来要面对的命运。她站在窗前看天气，街道很安静。"
    )

    report = evaluate_qimao_opening_gate(text, protagonist_name="沈姝")
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "ordinary_entry" in codes
    assert "flat_narration" in codes
    assert "first_10k_loop_missing" in codes


def test_qimao_opening_gate_fails_lore_memory_without_present_pressure() -> None:
    text = (
        "暮色从山脊漫下来时，苏砚踏上了青萝镇外的青石板路。"
        "他的目光先落在镇口那棵歪脖子老槐上。十二枚铜钱嵌在树根里，"
        "锈蚀边缘与树皮长成一体。苏砚蹲下身，左手按上旧疤。"
        "疤下藏着他与别人不同的地方：能感知器物的残痕，能听见死物的低语。"
        "指腹触及铜钱的瞬间，冰凉气流窜进血管。火光，不是现在的，"
        "是很久以前的。器灵共感从不免费。十二地支、镇宅、邪祟、器气、"
        "寻迹者、器魂和铭纹鼎的旧事同时涌上来。"
    )

    report = evaluate_qimao_opening_gate(text, protagonist_name="苏砚")
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "weak_present_conflict" in codes
    assert "retrospective_fake_conflict" in codes
    assert "opening_lore_overload" in codes


def test_qimao_opening_gate_fails_when_protagonist_absent_from_first_page() -> None:
    text = (
        "祠堂门口，二叔把账本按在火盆边，逼所有人签字，否则今晚就烧掉证据。"
        "族老们互相看了一眼，没有人敢出声。火星卷起，半页旧账被烧出黑边。"
        "门外忽然传来脚步声，所有人都回头。"
    )

    report = evaluate_qimao_opening_gate(text, protagonist_name="沈姝")
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "weak_immersion" in codes


def test_qimao_opening_gate_fails_conflict_free_chapter_ending() -> None:
    text = (
        "沈姝冲进祠堂，抓住账本，逼二叔承认旧案另有隐情。"
        "她夺回证据后，众人暂时散去。她回到房间，天色渐暗，一切暂时平静。"
    )

    report = evaluate_qimao_opening_gate(text, protagonist_name="沈姝")
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "weak_hook" in codes


def test_qimao_opening_gate_report_serializes_to_dict() -> None:
    report = evaluate_qimao_opening_gate("", protagonist_name="沈姝")
    payload = qimao_opening_gate_report_to_dict(report)

    assert payload["passed"] is False
    assert isinstance(payload["findings"], list)
    assert payload["findings"][0]["code"] in {
        "weak_immersion",
        "weak_hook",
        "first_10k_loop_missing",
    }
