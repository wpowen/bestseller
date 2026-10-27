# ruff: noqa: RUF001 -- Chinese prose fixtures intentionally use full-width punctuation.

from __future__ import annotations

from copy import deepcopy

import pytest

from bestseller.services.concept_contract import (
    build_concept_contract,
    require_story_architecture_for_operation,
    validate_concept_contract,
)
from bestseller.services.pipelines import (
    _prepare_story_architecture_migration_metadata,
)
from bestseller.services.planning_kernel import build_project_planning_kernel
from bestseller.services.story_viability import evaluate_story_viability

GENRE_CONTRACTS = {
    "异世界经营": [
        "source_world_profession",
        "target_world_dominant_rule_system",
        "cross_system_mapping_limits",
        "host_identity_obligation",
        "exposure_cost_model",
    ],
    "都市悬疑": [
        "fair_play_clue_path",
        "suspect_state_map",
        "rule_or_case_boundary",
        "revelation_ladder",
        "dread_or_pressure_curve",
    ],
    "古言权谋": [
        "power_map",
        "faction_interest",
        "information_asymmetry",
        "strategic_tradeoff",
        "world_rule_consequence",
    ],
    "科幻生存": [
        "science_constraint_model",
        "research_validation_chain",
        "energy_logistics",
        "mecha_or_fleet_state",
        "tactical_countermeasure_loop",
    ],
}


def test_otherworld_architecture_uses_identity_obligation_without_inventing_debt() -> None:
    from bestseller.services.concept_tournament import _build_engine_kernel_messages
    from bestseller.services.story_design_grammars import resolve_story_design_grammar

    grammar = resolve_story_design_grammar(genre="玄幻", sub_genre="异世大陆经营升级")
    _, prompt = _build_engine_kernel_messages(
        genre="玄幻",
        sub_genre="异世大陆",
        lane="职业处境#identity",
        chapter_count=60,
        authoritative_seed_concept=(
            "收费站班组长商洛穿越成废城城主，用旧界碑向狮人商队报价，带着七个老矿工后裔经营关卡。"
        ),
        cost_style="minimal",
    )

    assert "host_identity_obligation" in grammar.required_contracts
    assert "host_identity_debt" not in grammar.required_contracts
    assert "不得把上位创意未提供的前史性义务或追索关系生成为核心压力" in prompt
    assert "当下身份、处境、责任与关系推出危机" in prompt


def test_otherworld_architecture_migrates_legacy_identity_debt_contract() -> None:
    from bestseller.services.concept_tournament import _story_architecture_audit

    architecture = _architecture("异世界经营", comedy=True)
    legacy_contract = architecture["genre_contracts"].pop("host_identity_obligation")
    architecture["genre_contracts"]["host_identity_debt"] = legacy_contract
    card = {"story_architecture": architecture}

    audit = _story_architecture_audit(
        card,
        genre="玄幻",
        sub_genre="异世大陆",
        chapter_count=60,
        effect_skills=("comedy_engine",),
    )

    assert audit["passed"] is True, audit
    assert audit["architecture"]["genre_contracts"]["host_identity_obligation"] == legacy_contract


def _architecture(genre: str, *, comedy: bool = False) -> dict:
    required = GENRE_CONTRACTS[genre]
    units = []
    for index, (family, choice_mode, counter_mode, state_axis) in enumerate(
        (
            ("规则验证", "公开试探", "制度封锁", "规则认知"),
            ("资源争夺", "交换筹码", "断供围堵", "资源控制"),
            ("关系结盟", "承担责任", "离间策反", "同盟稳定"),
            ("秩序改写", "放弃垄断", "联合反扑", "权力结构"),
        ),
        start=1,
    ):
        units.append(
            {
                "id": f"u{index}",
                "input": f"第{index}阶段出现新局势",
                "goal": f"完成第{index}阶段可观察目标",
                "choice": f"主角采用{choice_mode}并放弃安全退路",
                "choice_mode": choice_mode,
                "mechanism_family": family,
                "counter": f"对手使用{counter_mode}改变行动条件",
                "counter_mode": counter_mode,
                "cost": f"失去第{index}项既有筹码",
                "cost_mode": f"筹码类型{index}",
                "state_change": f"{state_axis}从状态{index - 1}变为状态{index}",
                "state_axis": state_axis,
                "payoff": f"兑现第{index}类读者承诺",
                "next_pressure": f"状态{index}迫使下一阶段改变策略",
                "caused_by": "" if index == 1 else f"u{index - 1}",
            }
        )
    effects = []
    selected = []
    if comedy:
        selected = ["comedy_engine"]
        effects = [
            {
                "skill_key": "comedy_engine",
                "mechanism": "主角按现代流程严肃办事，本地权威把流程误读成挑战",
                "variation_sources": ["身份错位", "规则错位", "利益错位"],
                "consequence": "笑点改变交易条件和人物关系，而不是插科打诨",
                "phase_payoffs": ["误读成威胁", "误读成特权", "主角反用误读立规则"],
                "setup": "主角用正常流程解决问题",
                "misalignment": "异世界角色按本地权力规则错误理解",
                "reaction": "人物为维护身份做出过度反应",
                "situation_change": "反应反而给主角新的筹码并制造下一轮麻烦",
            }
        ]
    return {
        "schema_version": "story-architecture.v1",
        "target_chapters": 60,
        "grammar": {"key": genre, "required_contracts": required},
        "genre_contracts": {
            key: {
                "rule": f"{key}的可执行规则",
                "observable_state": f"{key}状态",
                "failure_consequence": f"违反{key}会改变后续局势",
            }
            for key in required
        },
        "selected_effect_skills": selected,
        "effect_contracts": effects,
        "causal_units": units,
        "story_engine": {
            "axes": ["经营", "升级", "冲突", "关系"],
            "endgame_direction": "主角必须在个人垄断与公开新秩序之间作出终局选择",
        },
        "progression": [
            {"name": "能力边界", "from": "只能观察", "to": "能够改写规则"},
            {"name": "公共地位", "from": "被驱逐者", "to": "秩序制定者"},
        ],
        "relationships": [
            {
                "a": "主角",
                "b": "盟友",
                "tension": "互相需要但争夺决定权",
                "initial_state": "交易合作",
                "turning_choice": "主角承担盟友本应承担的公开责任",
                "changed_state": "共同治理但保留否决权",
                "change": "从交易合作变成共同治理",
            }
        ],
        "conflicts": [
            {"actor": "地方既得利益者", "goal": "维持旧规则", "tactics": ["封锁", "收买"]},
            {"actor": "上层秩序代表", "goal": "收回解释权", "tactics": ["合法化围剿", "分化盟友"]},
        ],
        "opponent_adaptations": [
            {
                "after_unit": "u1",
                "actor": "地方既得利益者",
                "learned": "主角依赖公开验证获得信任",
                "strategy_change": "从否认规则改为制造伪证污染验证",
                "forces_new_choice": "主角必须在公开证据与保护证人之间取舍",
            },
            {
                "after_unit": "u2",
                "actor": "上层秩序代表",
                "learned": "断供会促使主角建立替代网络",
                "strategy_change": "从断供改为渗透并争夺网络解释权",
                "forces_new_choice": "主角必须开放网络或承担孤立成本",
            },
        ],
    }


def _concept_contract() -> dict:
    architecture = _architecture("异世界经营", comedy=True)
    winner = {
        "concept": "旧驿站账房穿进异界后，用两套互不兼容的通行规则重建边城秩序。",
        "mechanism": "每轮规则套利都会改变资源归属，引来会学习的新对手。",
        "hook_question": "当两套规则都被对手学会后，他还愿不愿公开解释权？",
        "protagonist_identity": "熟悉交通调度的异界边城代理人",
        "protagonist_private_desire": "建立一套不靠出身也能通行的秩序",
        "protagonist_flaw": "习惯把人当成可调度变量",
        "core_abnormality": "能看见两套规则对同一行为给出的不同价格与权限",
        "opening_crisis": "第一支商队拒绝进城且外部据点准备垄断路线",
        "opponent_system": "旧贵族、商队联盟和上层关务机构争夺解释权",
        "decision_proof": "退让会失去唯一商路，强抢会失去合法性，只能公开验证规则",
        "emotional_promise": "看一个讲流程的人被异界权力逻辑反复误读并反用误读破局",
        "repeatable_story_unit": "规则验证、主角选择、对手学习、状态改写",
        "unit_families": ["规则验证", "资源争夺", "关系结盟", "秩序改写"],
        "unit_frequency": "每2-4章完成一次选择与反制",
        "unit_count_estimate": 15,
        "progress_bar": "规则解释权从城门扩展到区域网络",
        "question_ladder": ["谁定价", "谁解释", "谁拥有规则"],
        "ch50": "区域联盟要求主角交出公开规则的最终解释权",
        "renewal_sources": ["新规则冲突", "对手学习"],
        "accumulation_tracks": ["解释权", "同盟稳定度"],
        "phase_transitions": ["验证规则", "争夺网络", "公开秩序"],
        "opposing_ecology": ["地方垄断者", "上层关务机构"],
        "endgame_direction": "个人垄断与公开秩序之间的终局选择",
        "story_architecture": architecture,
    }
    return build_concept_contract(
        winner=winner,
        story_spine={
            "who": winner["protagonist_identity"],
            "wants": winner["protagonist_private_desire"],
            "why_now": winner["opening_crisis"],
            "against": winner["opponent_system"],
            "stakes": winner["emotional_promise"],
            "question": winner["hook_question"],
        },
        target_chapters=60,
        genre="异世界",
        sub_genre="经营升级",
    )


@pytest.mark.parametrize("genre", tuple(GENRE_CONTRACTS))
def test_complete_story_architecture_passes_across_genres(genre: str) -> None:
    result = evaluate_story_viability(
        _architecture(genre, comedy=genre == "异世界经营"),
        target_chapters=60,
    )

    assert result["passed"] is True, result
    assert result["blocking_codes"] == []


def test_surface_variation_cannot_hide_one_repeated_causal_mechanism() -> None:
    story = _architecture("都市悬疑")
    for index, unit in enumerate(story["causal_units"], start=1):
        unit.update(
            {
                "input": f"不同地点出现第{index}条线索",
                "goal": f"调查第{index}个不同嫌疑人",
                "choice": f"用第{index}种说法询问",
                "counter": f"嫌疑人给出第{index}个借口",
                "cost": f"浪费第{index}小时",
                "state_change": f"线索数量增加到{index}",
                "payoff": f"找到第{index}条线索",
                "next_pressure": f"继续调查第{index + 1}人",
                "choice_mode": "询问嫌疑人",
                "mechanism_family": "发现线索",
                "counter_mode": "口头否认",
                "cost_mode": "消耗时间",
                "state_axis": "线索数量",
            }
        )

    result = evaluate_story_viability(story, target_chapters=60)

    assert result["passed"] is False
    assert "STATIC_LOOP_REPETITION" in result["blocking_codes"]


def test_selected_comedy_requires_an_executable_effect_contract() -> None:
    story = _architecture("异世界经营", comedy=True)
    story["effect_contracts"] = []

    result = evaluate_story_viability(story, target_chapters=60)

    assert result["passed"] is False
    assert "MISSING_SELECTED_EFFECT_CONTRACT" in result["blocking_codes"]


def test_comedy_label_alone_is_not_content_realization() -> None:
    story = _architecture("异世界经营")
    story["tags"] = ["异世界", "经营", "喜剧"]
    story["selected_effect_skills"] = ["comedy_engine"]

    result = evaluate_story_viability(story, target_chapters=60)

    assert result["passed"] is False
    assert "MISSING_SELECTED_EFFECT_CONTRACT" in result["blocking_codes"]


def test_grammar_catalog_does_not_count_as_consumed_contracts() -> None:
    story = _architecture("古言权谋")
    story["genre_contracts"] = {}

    result = evaluate_story_viability(story, target_chapters=60)

    assert result["passed"] is False
    assert "MISSING_GENRE_CONTRACT_IMPLEMENTATION" in result["blocking_codes"]


def test_opponent_must_learn_and_change_strategy() -> None:
    story = _architecture("科幻生存")
    broken = deepcopy(story["opponent_adaptations"])
    for item in broken:
        item["learned"] = ""
        item["strategy_change"] = ""
    story["opponent_adaptations"] = broken

    result = evaluate_story_viability(story, target_chapters=60)

    assert result["passed"] is False
    assert "MISSING_OPPONENT_ADAPTATION" in result["blocking_codes"]


def test_causal_units_must_form_a_dependency_chain() -> None:
    story = _architecture("都市悬疑")
    for unit in story["causal_units"][1:]:
        unit["caused_by"] = "unknown"

    result = evaluate_story_viability(story, target_chapters=60)

    assert result["passed"] is False
    assert "CAUSAL_CHAIN_DISCONNECTED" in result["blocking_codes"]


def test_architecture_is_sealed_into_concept_lineage_and_used_by_planning() -> None:
    contract = _concept_contract()

    assert contract["story_architecture_required"] is True
    assert contract["story_architecture"]["champion_id"] == contract["champion_id"]
    assert validate_concept_contract(contract, target_chapters=60) == []

    project = {
        "slug": "architecture-lineage-regression",
        "title": "跨题材回归样本",
        "genre": "异世界",
        "sub_genre": "经营升级",
        "target_chapters": 60,
        "metadata": {
            "concept_contract_version": "2",
            "concept_contract_required": True,
            "concept_contract": contract,
        },
    }
    kernel = build_project_planning_kernel(project)

    assert kernel["story_viability"]["passed"] is True
    assert kernel["story_viability"]["report"]["mode"] == "story-architecture.v1"
    assert require_story_architecture_for_operation(
        project["metadata"],
        target_chapters=60,
        operation="outline_replan",
    )

    for unit in contract["story_architecture"]["causal_units"]:
        unit.update(
            {
                "choice_mode": "公开试探",
                "mechanism_family": "规则验证",
                "counter_mode": "制度封锁",
                "cost_mode": "筹码类型1",
                "state_axis": "规则认知",
            }
        )
    violations = validate_concept_contract(contract, target_chapters=60)
    assert any("input_hash" in item for item in violations)
    assert any("STATIC_LOOP_REPETITION" in item for item in violations)


def test_reconception_migration_replaces_truth_and_keeps_audit_lineage() -> None:
    contract = _concept_contract()

    merged, promoted = _prepare_story_architecture_migration_metadata(
        {
            "keep_runtime_flag": True,
            "concept_contract": {
                "champion_id": "legacy-champion",
                "input_hash": "legacy-hash",
            },
        },
        {"concept_contract": contract, "premise": "新构思真源"},
        target_chapters=60,
        operation="foundation_rebuild",
    )

    assert promoted["champion_id"] == contract["champion_id"]
    assert merged["concept_contract"] == promoted
    assert merged["keep_runtime_flag"] is True
    assert merged["story_architecture_migration_status"] == "validated"
    audit = merged["story_architecture_migration_history"][-1]
    assert audit["previous_champion_id"] == "legacy-champion"
    assert audit["new_champion_id"] == contract["champion_id"]
    assert audit["operation"] == "foundation_rebuild"


def test_structural_replan_cannot_use_legacy_seriality_proof_as_architecture() -> None:
    with pytest.raises(Exception) as exc_info:
        require_story_architecture_for_operation(
            {
                "concept_contract_version": "2",
                "concept_contract": {
                    "story_architecture_required": False,
                    "seriality_proof": {"repeatable_story_unit": "重复单元"},
                },
            },
            target_chapters=60,
            operation="outline_replan",
        )

    assert "story_architecture.v1" in str(exc_info.value)
    assert "禁止用旧连载说明" in str(exc_info.value)
