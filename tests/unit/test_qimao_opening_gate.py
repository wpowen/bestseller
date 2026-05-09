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


def test_opening_gate_handles_english_conflict_opening() -> None:
    chapters = {
        1: (
            "Mara reached the vault as the officer shoved her father's ledger toward the fire. "
            "\"Sign, or the evidence dies with him.\" She grabbed the cover, burned her palm, "
            "and used the torn seal to force him back. She had the first proof, but then the lock "
            "on the dead man's room clicked behind her. Who still had that key?"
        ),
        2: (
            "Mara chased the courier into the alley, dodged the knife, and discovered the same seal "
            "inside his glove. She gained leverage, but exposed that she could read the hidden accounts."
        ),
        3: (
            "Mara blocked the carriage, recovered the missing page, and unlocked the first layer of proof. "
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
