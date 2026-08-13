# ruff: noqa: RUF001
from __future__ import annotations

from bestseller.services.concept_contract import (
    apply_concept_contract_to_book_spec,
    build_concept_contract,
    reseal_concept_contract_lineage,
    render_concept_contract_block,
    render_volume_seriality_execution_block,
    require_conception_contract_for_target,
    require_valid_concept_contract,
    validate_concept_contract,
)
from bestseller.services.logline_gate import (
    LoglineAction,
    verdict_from_approved_concept_contract,
)

WINNER = {
    "concept": "收殓师能继承枉死者未发生的未来；第一份未来要求他三天后杀死陌生新郎。",
    "mechanism": "每卷处理一桩被盗余生案件，并改变未来资源的归属",
    "repeatable_story_unit": "追查一桩余生异常、争夺归属并留下不可逆后果",
    "hook_question": "死者为什么把杀人未来留给他？",
    "protagonist_identity": "能看见死者未来的收殓师林默",
    "protagonist_private_desire": "查清母亲被偷走的二十年余生",
    "protagonist_flaw": "习惯独自验证证据，不愿把风险交给别人",
    "core_abnormality": "完成枉死者真实遗愿并入殓后，会随机继承其未发生的未来",
    "opening_crisis": "首具女尸留下的未来要求他三天内阻止一场婚礼谋杀",
    "opponent_system": "把未来作为资源交易、会销毁异常死亡证据的明日会",
    "decision_proof": (
        "报警只能得到已结案的意外认定，逃避会让谋杀转移给无辜者；"
        "亲自进入婚礼既能验证未来也能阻止死亡"
    ),
    "emotional_promise": "每次替死者讨回明天，都逼活人回答自己愿为未来付什么",
    "unit_families": ["查验死亡", "追踪交易", "关系抉择", "公开争夺"],
    "unit_frequency": "每2-4章完成一次选择与反制",
    "unit_count_estimate": 180,
    "question_ladder": ["个案为何偏离命运", "谁在回收余生", "谁控制全城明天"],
    "renewal_sources": ["异常死亡持续进入殡仪馆", "余生黑市持续制造交易"],
    "accumulation_tracks": ["主角的余生权限", "明日会暴露层级"],
    "phase_transitions": [
        "第1-80章处理单案",
        "第81-180章介入城市黑市",
        "第181-350章跨城争夺",
        "第351-500章未来制度战争",
    ],
    "opposing_ecology": ["余生掮客", "明日会", "命运监管者"],
    "endgame_direction": "决定未来是否还能成为可交易资源",
    "judge_freshness": 8.0,
    "judge_click": 8.5,
    "judge_predictable": 4.0,
    "judge_character_logic": 8.0,
    "judge_mechanism_causality": 8.0,
    "judge_genre_fidelity": 8.5,
    "judge_plain_language": 8.0,
    "judge_story_motion": 8.0,
    "composite": 8.1,
    "rejected_reason": None,
    "seriality_judge": {
        "renewability": 8.0,
        "escalation": 8.0,
        "anti_reset": 8.0,
        "coherence": 8.0,
        "promise_survival": 8.0,
        "unit_density": 8.0,
        "reason": "可持续",
    },
}

SPINE = {
    "who": "能看见死者未来的收殓师林默",
    "wants": "查清第一具女尸为何把刺杀未来留给自己",
    "why_now": "婚礼将在三天后举行",
    "against": "购买他人未来的明日会",
    "stakes": "他会继承一场无法摆脱的谋杀",
    "question": "他能否查出谁偷走了死者的余生？",
}


def test_contract_has_one_lineage_and_passes_capacity() -> None:
    contract = build_concept_contract(
        winner=WINNER,
        story_spine=SPINE,
        target_chapters=500,
        genre="悬疑",
        sub_genre="都市奇幻",
    )

    assert contract["hook_card"]["one_liner"] == WINNER["concept"]
    assert contract["seriality_proof"]["capacity_report"]["passed"] is True
    assert contract["story_spine"]["schema_version"] == "story-spine.v2"
    assert contract["story_spine"]["who"] == WINNER["protagonist_identity"]
    assert validate_concept_contract(contract, target_chapters=500) == []
    ids = {
        contract["champion_id"],
        contract["hook_card"]["champion_id"],
        contract["seriality_proof"]["champion_id"],
        contract["story_spine"]["champion_id"],
    }
    assert len(ids) == 1


def test_identity_migration_reseals_all_concept_lineage_hashes() -> None:
    contract = build_concept_contract(
        winner=WINNER,
        story_spine=SPINE,
        target_chapters=500,
        genre="悬疑",
        sub_genre="都市奇幻",
    )
    old_hash = contract["input_hash"]
    contract["hook_card"]["protagonist"] = contract["hook_card"][
        "protagonist"
    ].replace("林默", "沉骨")
    contract["story_spine"]["who"] = contract["story_spine"]["who"].replace(
        "林默", "沉骨"
    )

    assert any(
        "input_hash" in violation
        for violation in validate_concept_contract(contract, target_chapters=500)
    )

    resealed = reseal_concept_contract_lineage(contract, target_chapters=500)

    assert resealed["input_hash"] != old_hash
    assert validate_concept_contract(resealed, target_chapters=500) == []
    assert {
        resealed["champion_id"],
        resealed["hook_card"]["champion_id"],
        resealed["seriality_proof"]["champion_id"],
        resealed["story_spine"]["champion_id"],
    } == {resealed["champion_id"]}


def test_approved_contract_is_authoritative_logline_evidence() -> None:
    contract = build_concept_contract(
        winner=WINNER,
        story_spine=SPINE,
        target_chapters=500,
        genre="悬疑",
        sub_genre="都市奇幻",
    )

    judged_one_liner = str(contract["hook_card"]["one_liner"])
    verdict = verdict_from_approved_concept_contract(
        contract,
        target_chapters=500,
        # 复用只对「被审过的那句原文」成立——通行证认文本，不认书。
        logline_text=judged_one_liner,
    )

    assert verdict is not None
    assert verdict.action is LoglineAction.EXPAND
    assert verdict.llm_used is True
    assert verdict.weakest_axis == "concept_contract_evidence"


def test_rewritten_logline_cannot_ride_the_champions_verdict() -> None:
    """证据转移要求文本同一（2026-08-11 真机《摆摊求死》）。

    淘汰赛审过的是钩子卡 one_liner（大白话、因果完整）；T6 又从冠军简介另写了
    一句（天煞孤星/真心人/「杀一回」病句、因果断裂）塞进 market.logline。复用
    分支此前不接收文本参数，把原句的 12 轴全 4.0 通行证盖到了改写稿上，还标
    ``llm_used=True``——没有任何 LLM 读过那句话。改写稿必须交给真判官。
    """

    contract = build_concept_contract(
        winner=WINNER,
        story_spine=SPINE,
        target_chapters=500,
        genre="悬疑",
        sub_genre="都市奇幻",
    )

    assert verdict_from_approved_concept_contract(
        contract,
        target_chapters=500,
        logline_text="修了八百多年从没惦记过谁的天煞孤星，被逼着交出一个真心人的名字。",
    ) is None
    # 不带文本的旧式调用同样不许放行——宁可请真判官，不可默认同一。
    assert verdict_from_approved_concept_contract(
        contract,
        target_chapters=500,
    ) is None
    # 空白差异不算改写。
    spaced = "  " + str(contract["hook_card"]["one_liner"]) + "\n"
    assert verdict_from_approved_concept_contract(
        contract, target_chapters=500, logline_text=spaced
    ) is not None


def test_contract_without_tournament_quality_cannot_bypass_logline_judge() -> None:
    winner = {key: value for key, value in WINNER.items() if not key.startswith("judge_")}
    winner.pop("seriality_judge")
    contract = build_concept_contract(
        winner=winner,
        story_spine=SPINE,
        target_chapters=500,
        genre="悬疑",
        sub_genre="都市奇幻",
    )

    assert verdict_from_approved_concept_contract(
        contract,
        target_chapters=500,
        logline_text=str(contract["hook_card"]["one_liner"]),
    ) is None


def test_contract_rejects_target_or_lineage_tampering() -> None:
    contract = build_concept_contract(
        winner=WINNER,
        story_spine=SPINE,
        target_chapters=500,
        genre="悬疑",
        sub_genre="都市奇幻",
    )
    contract["hook_card"]["champion_id"] = "other"

    violations = validate_concept_contract(contract, target_chapters=1000)

    assert any("champion_id" in item for item in violations)
    assert any("target_chapters" in item for item in violations)


def test_contract_applies_serial_engine_without_hook_cost_pollution() -> None:
    contract = build_concept_contract(
        winner=WINNER,
        story_spine=SPINE,
        target_chapters=500,
        genre="悬疑",
        sub_genre="都市奇幻",
    )
    book = apply_concept_contract_to_book_spec(
        {"series_engine": {"reader_promise": "保留模型写出的读者承诺"}},
        contract,
    )

    assert book["series_engine"]["reader_promise"] == "保留模型写出的读者承诺"
    assert book["series_engine"]["repeatable_story_unit"] == WINNER["repeatable_story_unit"]
    assert book["series_engine"]["unit_families"] == WINNER["unit_families"]
    assert book["series_engine"]["phase_transitions"] == WINNER["phase_transitions"]
    assert "cost_engine" not in book["series_engine"]
    assert "可再生故事单元" in render_concept_contract_block(contract)


def test_source_bound_book_keeps_contract_as_lineage_only() -> None:
    contract = build_concept_contract(
        winner=WINNER,
        story_spine=SPINE,
        target_chapters=500,
        genre="悬疑",
        sub_genre="都市奇幻",
    )
    book = apply_concept_contract_to_book_spec(
        {
            "series_engine": {
                "reader_promise": "锁定创建快照中的承诺",
                "repeatable_story_unit": "锁定创建快照中的故事引擎",
                "unit_families": [],
                "mystery_ladder": [],
            },
            "_meta": {"source_bound_design": True},
        },
        contract,
    )

    assert book["series_engine"] == {
        "reader_promise": "锁定创建快照中的承诺",
        "repeatable_story_unit": "锁定创建快照中的故事引擎",
        "unit_families": [],
        "mystery_ladder": [],
    }
    assert book["concept_contract_lineage"]["champion_id"] == contract["champion_id"]


def test_seriality_proof_tampering_invalidates_lineage_hash() -> None:
    contract = build_concept_contract(
        winner=WINNER,
        story_spine=SPINE,
        target_chapters=500,
        genre="悬疑",
        sub_genre="都市奇幻",
    )
    contract["seriality_proof"]["unit_families"][0] = "换成未审批的新玩法"

    violations = validate_concept_contract(contract, target_chapters=500)

    assert any("input_hash" in item for item in violations)


def test_core_story_seed_overrides_later_spine_drift() -> None:
    drifted_spine = {
        **SPINE,
        "who": "突然换成一名天才刑警",
        "wants": "拯救世界",
        "why_now": "无因倒计时开始",
        "against": "临时出现的魔王",
    }
    contract = build_concept_contract(
        winner=WINNER,
        story_spine=drifted_spine,
        target_chapters=500,
        genre="悬疑",
        sub_genre="都市奇幻",
    )

    spine = contract["story_spine"]
    assert spine["who"] == WINNER["protagonist_identity"]
    assert spine["wants"] == WINNER["protagonist_private_desire"]
    assert spine["why_now"] == WINNER["opening_crisis"]
    assert spine["against"] == WINNER["opponent_system"]


def test_long_conception_cannot_continue_without_approved_contract() -> None:
    try:
        require_conception_contract_for_target({}, target_chapters=500)
    except Exception as exc:
        assert getattr(exc, "code", "") == "concept_contract_invalid"
        assert "500" in str(exc)
    else:
        raise AssertionError("500-chapter conception must fail closed without a contract")

    assert require_conception_contract_for_target({}, target_chapters=80) is None


def test_new_long_project_marker_cannot_bypass_contract_validation() -> None:
    try:
        require_valid_concept_contract(
            {"concept_contract_required": True},
            target_chapters=500,
        )
    except Exception as exc:
        assert getattr(exc, "code", "") == "concept_contract_invalid"
        assert "缺失" in str(exc)
    else:
        raise AssertionError("marked long-form project must not enter planning without contract")


def test_volume_execution_block_carries_growth_not_global_hook_repetition() -> None:
    contract = build_concept_contract(
        winner=WINNER,
        story_spine=SPINE,
        target_chapters=500,
        genre="悬疑",
        sub_genre="都市奇幻",
    )
    block = render_volume_seriality_execution_block(
        contract,
        {
            "seriality_phase_id": "phase-02",
            "seriality_phase_ref": "介入城市黑市",
            "unit_family_ref": "追踪交易",
            "renewable_unit_variant": "从单案调查变为黑市交易反制",
            "accumulation_track_deltas": [
                {
                    "track_ref": "主角的余生权限",
                    "delta": "主角的余生权限：从看见片段变为追踪交易路径",
                }
            ],
        },
    )

    assert "介入城市黑市" in block
    assert "seriality_contract" in block
    assert "禁止重启开篇钩子" in block
    assert WINNER["concept"] not in block
