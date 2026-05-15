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


def _emotion_kernel() -> dict[str, object]:
    return {
        "version": 1,
        "reader_emotion_promise": "读者追看沈姝如何在旧案压力中赢下一步，并承担身份暴露的代价。",
        "primary_reader_waiting": ["旧案真相何时爆开", "沈姝能否保住母亲"],
        "empathy_contracts": [
            {
                "contract_id": "opening-desire",
                "character_key": "沈姝",
                "chapter_range": "1-3",
                "situation": "旧案证据被夺走。",
                "current_desire": "夺回账页并保护母亲。",
                "fear_or_loss": "失败会失去救母亲的机会。",
                "flaw_pressure": "她习惯独自承担风险。",
                "sensory_entry": "门外脚步声和潮湿账页的霉味。",
                "judgment_logic": "她只能在证据残缺时先判断谁可信。",
                "emotional_reaction": "恐惧和愤怒同时涌上来。",
                "reasonable_action": "先夺回印章，再转移母亲。",
                "consequence": "抢回印章，但暴露身份的代价不可撤销。",
            },
            {
                "contract_id": "midpoint-desire",
                "character_key": "沈姝",
                "chapter_range": "4-6",
                "situation": "港务官开始追查她的暗线。",
                "current_desire": "拿到旧案主账。",
                "fear_or_loss": "失败会让母亲和证人一起被灭口。",
                "flaw_pressure": "她仍想独自换取安全。",
                "sensory_entry": "码头铁门被风拍响。",
                "judgment_logic": "她知道继续藏身已经没有意义。",
                "emotional_reaction": "她心口发冷，却不再退。",
                "reasonable_action": "主动把假账送进港务署。",
                "consequence": "赢下码头局面，但失去旧身份。",
            },
        ],
        "bomb_contracts": [
            {
                "bomb_id": "ledger-bomb",
                "bomb_type": "danger",
                "chapter_range": "1-2",
                "reader_knows": "读者知道账页被港务官提前换过。",
                "character_blindspot": "沈姝只看到被夺走的账页。",
                "danger": "她拿错账页会暴露母亲藏身处。",
                "trigger_condition": "她把账页交给证人。",
                "countdown": "两章内。",
                "consequence": "身份暴露的代价。",
                "payoff_window": "3-4",
                "rational_ignorance": "账页印泥和真账一致，短时间无法细查。",
                "escalation_steps": ["证人迟到", "账页缺角", "港务官提前到场"],
            }
        ],
        "antagonist_moral_contracts": [
            {
                "antagonist_key": "港务官",
                "chapter_range": "1-6",
                "public_mask": "维护港城秩序的清官。",
                "real_good_deeds": ["曾救过码头工人"],
                "hidden_desire": "控制所有旧案证据。",
                "fear_of_loss": "害怕旧案毁掉声望。",
                "cracks": ["只在结果有利于自己时强调大局"],
                "first_boundary_crossing": "以保护证人为名软禁证人。",
                "self_justification": "没有他的控制，港城会乱。",
                "collapse_wound": "不能承认所谓秩序只是自保。",
                "target_reader_response": "恨他但记住他的恐惧。",
            }
        ],
        "ending_texture_contract": {
            "ending_type": "HE",
            "core_wish_fulfilled": "沈姝洗清母亲冤屈。",
            "relationship_settlement": "母女重新并肩生活。",
            "irreversible_cost_retained": "旧身份和错过的年月不会复原。",
            "theme_answer": "真正的胜利是承认伤痕后继续选择自由。",
            "future_open": "她们离开港城，仍保留追查下一案的可能。",
        },
        "emotion_chain": [
            {
                "chapter_range": "1-2",
                "target_reader_emotion": "焦虑",
                "reader_waiting_for": "账页陷阱何时触发。",
                "reader_worry": "沈姝会把假账交出去。",
                "pressure_source": "港务官的换账局。",
                "payoff_or_aftereffect": "陷阱逼近并留下更大的债。",
                "callback": "潮湿账页",
            },
            {
                "chapter_range": "3-4",
                "target_reader_emotion": "心疼",
                "reader_waiting_for": "沈姝能否救下证人。",
                "reader_worry": "母亲藏身处会暴露。",
                "pressure_source": "证人和母亲不能同时保住。",
                "payoff_or_aftereffect": "关键线索兑现，但身份暴露的代价扩大。",
                "callback": "印章缺角",
            },
            {
                "chapter_range": "5-6",
                "target_reader_emotion": "满足中带怅然",
                "reader_waiting_for": "旧案主账是否公开。",
                "reader_worry": "她会失去旧身份。",
                "pressure_source": "港务署封口。",
                "payoff_or_aftereffect": "主账公开，同时旧身份回不去。",
                "callback": "空白传票",
            },
        ],
        "callback_motifs": ["潮湿账页", "空白传票"],
    }


def test_whole_book_quality_gate_passes_good_multi_chapter_sample() -> None:
    chapters = {idx: _good_chapter("沈姝", idx) for idx in range(1, 7)}

    report = evaluate_whole_book_quality(chapters)

    assert report.passed is True
    assert report.findings == ()
    assert len(report.ledger) == 6
    assert report.metrics["flat_chapter_count"] == 0
    assert {record.functional_shape for record in report.ledger} == {"proactive_scene"}


def test_whole_book_quality_gate_tracks_emotion_kernel_telemetry() -> None:
    chapters = {idx: _good_chapter("沈姝", idx) for idx in range(1, 7)}

    report = evaluate_whole_book_quality(
        chapters,
        emotion_driven_kernel=_emotion_kernel(),
    )

    emotion_metrics = report.metrics["emotion_driven"]
    assert report.passed is True
    assert report.findings == ()
    assert emotion_metrics["available"] is True
    assert emotion_metrics["visible_desire_chapter_count"] == 6
    assert emotion_metrics["irreversible_cost_chapter_count"] == 6
    assert emotion_metrics["ending_texture_ready"] is True
    assert emotion_metrics["bomb_setup_payoff_distances"][0]["distance"] == 1


def test_whole_book_quality_gate_flags_stale_emotion_bomb() -> None:
    chapters = {
        1: _good_chapter("沈姝", 1),
        2: (
            "沈姝在第2章被威胁，必须赶到码头。"
            "她冲出巷口拦住追兵，决定先拖住对方。"
            "章末，门外突然传来脚步声，下一步只能硬闯？"
        ),
        3: (
            "沈姝在第3章继续被逼到仓库门口。"
            "她抓住门框挡住对方，选择把证人先藏进船舱。"
            "章末，港务官突然现身，谁把位置泄露出去？"
        ),
        4: _good_chapter("沈姝", 4),
    }
    kernel = _emotion_kernel()
    kernel["bomb_contracts"] = [
        {
            **kernel["bomb_contracts"][0],
            "bomb_id": "missing-payoff",
            "chapter_range": "1",
            "payoff_window": "2-3",
        }
    ]

    report = evaluate_whole_book_quality(chapters, emotion_driven_kernel=kernel)
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "emotion_bomb_payoff_stale" in codes
    assert report.metrics["emotion_driven"]["stale_bomb_ids"] == ["missing-payoff"]
    assert whole_book_quality_strategy_for_findings(report.findings) == (
        "emotion_bomb_payoff_reseed"
    )


def test_whole_book_quality_gate_flags_flat_chain_and_cost_free_win() -> None:
    chapters = {idx: _good_chapter("沈姝", idx) for idx in range(1, 7)}
    kernel = _emotion_kernel()
    kernel["ending_texture_contract"] = {
        **kernel["ending_texture_contract"],
        "irreversible_cost_retained": "",
    }
    kernel["emotion_chain"] = [
        {
            "chapter_range": "1-2",
            "target_reader_emotion": "爽",
            "reader_waiting_for": "沈姝赢下第一局。",
            "reader_worry": "对手会逃。",
            "pressure_source": "港务官压迫。",
            "payoff_or_aftereffect": "沈姝赢了。",
            "callback": "",
        },
        {
            "chapter_range": "3-4",
            "target_reader_emotion": "爽",
            "reader_waiting_for": "沈姝继续赢。",
            "reader_worry": "旧案会反扑。",
            "pressure_source": "旧案压力。",
            "payoff_or_aftereffect": "沈姝再次成功。",
            "callback": "",
        },
        {
            "chapter_range": "5-6",
            "target_reader_emotion": "爽",
            "reader_waiting_for": "沈姝最后翻盘。",
            "reader_worry": "港务官会反扑。",
            "pressure_source": "终局压迫。",
            "payoff_or_aftereffect": "沈姝翻盘成功。",
            "callback": "",
        },
    ]

    report = evaluate_whole_book_quality(chapters, emotion_driven_kernel=kernel)
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "emotion_flat_chain" in codes
    assert "emotion_cost_free_win" in codes
    assert "emotion_ending_texture_not_ready" in codes
    assert report.metrics["emotion_driven"]["repeated_emotion_modes"][0]["streak"] == 3


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
