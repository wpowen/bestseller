# ruff: noqa: RUF001

from __future__ import annotations

import pytest

from bestseller.services.whole_book_quality_gate import (
    build_whole_book_quality_rewrite_instructions,
    evaluate_whole_book_quality,
    whole_book_quality_report_to_dict,
    whole_book_quality_strategy_for_findings,
)

pytestmark = pytest.mark.unit


def _good_chapter(name: str, n: int) -> str:
    return (
        f"{name}在第{n}章刚进门就被新的证据逼到墙边，对手夺走账页，威胁她必须让步。"
        f"她没有后退，抓住对方话里的漏洞反制，抢回一枚关键印章。"
        f"这次小胜让她拿到筹码，却也付出暴露身份的代价。"
        f"章末，门外突然响起新的脚步声，真正拿走账本的人是谁？"
    )


def test_whole_book_quality_gate_passes_good_multi_chapter_sample() -> None:
    chapters = {idx: _good_chapter("沈姝", idx) for idx in range(1, 7)}

    report = evaluate_whole_book_quality(chapters)

    assert report.passed is True
    assert report.findings == ()
    assert len(report.ledger) == 6
    assert report.metrics["flat_chapter_count"] == 0
    assert {record.functional_shape for record in report.ledger} == {"proactive_scene"}


def test_whole_book_quality_gate_accepts_reactive_sequel_chapter() -> None:
    chapters = {
        1: _good_chapter("沈姝", 1),
        2: (
            "沈姝抱着抢回的印章坐到天亮，心口发冷。她终于明白旧案不是单独的仇怨，"
            "而是整座港城都在逼她沉默。若去报官，母亲会先被灭口；若继续藏着，"
            "证据也会失效。她最终决定天亮前去找港务官，先换母亲一夜平安。"
        ),
    }

    report = evaluate_whole_book_quality(chapters)

    assert report.passed is True
    assert report.findings == ()
    assert report.ledger[1].functional_shape == "reactive_sequel"


def test_whole_book_quality_gate_accepts_artifact_reveal_chapter() -> None:
    report = evaluate_whole_book_quality(
        {
            1: (
                "苏砚在姜家祖坟挖开石椁，掌心旧疤滴血，封印松动后九道编钟同时响起。"
                "他取出两枚相叠铜镜，确认铭纹鼎不在椁中，却发现姜沉璧留下的债仍在反噬。"
                "甬道深处有人提灯现身，笑着说：姜沉璧等的不是你取走铭纹鼎，"
                "而是等你把铭纹鼎亲手送回来。"
            )
        }
    )

    assert report.passed is True
    assert report.findings == ()
    assert report.ledger[0].functional_shape in {"proactive_scene", "reveal_turn"}


def test_whole_book_quality_gate_fails_flat_chapter_function() -> None:
    chapters = {
        1: _good_chapter("沈姝", 1),
        2: "沈姝回到房间，想了很多过去的事情。天色渐暗，她觉得一切都很复杂。",
    }

    report = evaluate_whole_book_quality(chapters)
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "chapter_function_missing" in codes


def test_whole_book_quality_gate_requires_signing_zone_hook_density() -> None:
    chapters = {
        idx: (
            f"沈姝在第{idx}章得知旧案证实了新的秘密，心口发冷。"
            "她在报官和隐藏证据之间反复权衡，母亲的安危压在她肩上。"
            "她最终决定先去找港务官，换母亲一夜平安。"
        )
        for idx in range(1, 11)
    }

    report = evaluate_whole_book_quality(chapters)
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "early_retention_hook_density_low" in codes
    assert report.metrics["retention_zones"]["signing_zone"]["hook_density"] == 0


def test_whole_book_quality_gate_requires_signing_zone_turn_density() -> None:
    chapters = {
        idx: (
            f"沈姝在第{idx}章被威胁，必须赶到码头。"
            "对手逼她立刻交人，她选择先守住入口。"
            "章末，门外突然响起新的脚步声，下一步只能抢先出门？"
        )
        for idx in range(1, 11)
    }

    report = evaluate_whole_book_quality(chapters)
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "early_retention_turn_density_low" in codes
    assert report.metrics["retention_zones"]["signing_zone"]["turn_density"] == 0


def test_whole_book_quality_gate_accepts_strong_signing_zone_mix() -> None:
    chapters = {}
    for idx in range(1, 13):
        if idx % 3 == 0:
            chapters[idx] = (
                f"沈姝在第{idx}章终于看出账页暗号，发现旧案真相只露出一角。"
                "她心口发冷，却决定立刻去截下一封密信。"
                "章末，门外突然有人低声说，密信已经被换走了？"
            )
        elif idx % 3 == 1:
            chapters[idx] = _good_chapter("沈姝", idx)
        else:
            chapters[idx] = (
                f"沈姝在第{idx}章夺回关键证据，也暴露了自己能读暗账的秘密。"
                "她疼得指尖发颤，仍选择继续查下去。"
                "章末，港务官突然递来一张空白传票，谁在提前布局？"
            )

    report = evaluate_whole_book_quality(chapters)

    assert report.passed is True
    assert report.findings == ()
    assert report.metrics["retention_zones"]["signing_zone"]["hook_density"] >= 0.75


def test_whole_book_quality_gate_fails_rolling_payoff_gap() -> None:
    chapters = {
        idx: (
            f"沈姝在第{idx}章被威胁，被迫继续奔走。"
            "她抓住门框挡住对方逼迫，冲出巷口，但没有拿到任何筹码。"
            "章末，门外突然有人敲门，新的危险逼近？"
        )
        for idx in range(1, 7)
    }

    report = evaluate_whole_book_quality(chapters, rolling_window=6)
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "rolling_payoff_gap" in codes


def test_whole_book_quality_gate_fails_repeated_openings() -> None:
    chapters = {
        idx: (
            "沈姝刚进门就被新的证据逼到墙边，对手夺走账页。"
            f"第{idx}章里她抓住漏洞反制，拿到筹码，也暴露身份。"
            "章末，门外突然响起新的脚步声，真正拿走账本的人是谁？"
        )
        for idx in range(1, 5)
    }

    report = evaluate_whole_book_quality(chapters, rolling_window=4)
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "rolling_repetition" in codes


def test_whole_book_quality_gate_fails_volume_without_momentum() -> None:
    chapters = {
        1: _good_chapter("沈姝", 1),
        2: _good_chapter("沈姝", 2),
        3: "沈姝终于回到房间，整理了一天的想法。她望着窗外，感觉事情还没有结束。",
    }
    volume_plan = [{"volume_number": 1, "arc_ranges": [[1, 3]], "chapter_count_target": 3}]

    report = evaluate_whole_book_quality(chapters, volume_plan=volume_plan)
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "volume_momentum_drop" in codes


def test_whole_book_quality_report_serializes_to_dict() -> None:
    report = evaluate_whole_book_quality({1: "她回到房间，想了很多。"})
    payload = whole_book_quality_report_to_dict(report)

    assert payload["passed"] is False
    assert isinstance(payload["ledger"], list)
    assert payload["findings"][0]["code"] == "chapter_function_missing"
    assert payload["ledger"][0]["functional_shape"] == "flat"


def test_whole_book_rewrite_instruction_maps_findings() -> None:
    report = evaluate_whole_book_quality({1: "她回到房间，想了很多。"})
    strategy = whole_book_quality_strategy_for_findings(report.findings)
    instructions = build_whole_book_quality_rewrite_instructions(
        report.findings,
        chapter_number=1,
        opening_quality_contract={
            "first_10000_loop": "trigger -> action -> reward/cost -> next hook"
        },
    )

    assert strategy == "chapter_function_rewrite"
    assert "不是润色任务" in instructions
    assert "不要求每章同构" in instructions
    assert "trigger -> action -> reward/cost -> next hook" in instructions
    assert "chapter_function_missing" in instructions
