# ruff: noqa: RUF001
from __future__ import annotations

import pytest

from bestseller.services.worldview_progression_gate import (
    evaluate_worldview_progression_gate,
    worldview_progression_report_to_dict,
)

pytestmark = pytest.mark.unit


def _story_design_kernel() -> dict[str, object]:
    return {
        "version": 1,
        "shape": {
            "length_class": "long",
            "publication_mode": "web_serial",
            "outline_depth": "chapter",
            "primary_duties": ["forward_pull", "visible_system_change"],
            "ending_contract": "close current loop while opening next desire",
        },
        "reader_promise": "每卷都让世界秩序、资源风险和权威压力发生可见升级。",
        "premise_contract": {
            "unique_hook": "旧航线规则能解释新帝国边境异常",
            "core_question": "主角能否把旧规则转化成新秩序中的生存优势?",
            "commercial_pull": "规则套利、资产暴露和权威追踪同步升级。",
            "forbidden_defaults": ["神秘玉佩"],
        },
        "character_conflict_contracts": [
            {
                "character_key": "protagonist",
                "external_goal": "找回被篡改的边境航线记录",
                "internal_need": "承认旧体系无法直接照搬",
                "pressure_source": "帝国审计庭",
                "choice_axis": "隐藏知识还是公开规则差异",
                "change_vector": "世界理解变化",
            }
        ],
        "world_conflict_contracts": [
            {
                "axis": "航线规则解释权",
                "rule": "旧帝国航线规则与新帝国审计法互相冲突。",
                "visible_cost": "每次解释旧规则都会引来审计记录。",
                "escalation_path": "从码头盘问升级到边境封锁。",
            }
        ],
        "worldview_kernel": {
            "premise": "边境世界的航线、资产和权威都围绕规则解释权运转。",
            "uniqueness_principle": "每次世界观使用都必须改变状态变量、资产风险或权威压力。",
            "invariants": [
                {
                    "key": "route_rule_arbitrage",
                    "rule": "旧航线知识只能通过新审计法的漏洞产生作用。",
                    "violation_cost": "照搬旧规则会触发帝国审计。",
                    "narrative_use": "把世界规则变成选择与代价。",
                }
            ],
            "systems": [
                {
                    "name": "边境航线审计体系",
                    "operating_logic": "航线解释权由审计庭、港务官和导航员三方争夺。",
                    "resources_or_authority": "航线档案、审计令、港口通行权。",
                    "limits": "公开使用旧知识会留下审计痕迹。",
                    "costs": "每次破解都会增加权威注意力。",
                    "failure_modes": ["档案封存", "港口封锁"],
                }
            ],
            "factions": [
                {
                    "name": "帝国审计庭",
                    "public_role": "维护航线记录合法性。",
                    "hidden_agenda": "掩盖篡改边境记录的旧案。",
                    "resources": "审计令、港口封锁权。",
                    "pressure_on_protagonist": "迫使主角公开旧体系知识来源。",
                }
            ],
            "locations": [
                {
                    "name": "灰港审计厅",
                    "surface_function": "公开听证和航线核验地点。",
                    "hidden_function": "让规则冲突和权威压力同时显形。",
                    "conflict_sources": ["档案核验", "审计封锁"],
                    "evidence_or_resource_types": ["航线档案", "审计记录"],
                }
            ],
            "reveal_ladder": [
                {
                    "stage": "volume-2",
                    "reveal": "帝国审计庭曾主动篡改边境航线。",
                    "earliest_volume": 2,
                    "unlock_condition": "主角先证明局部审计异常。",
                }
            ],
            "integration_contract": {
                "chapter_rule": "每章至少让一个世界规则通过选择、证据或代价落地。",
                "volume_rule": "每卷关闭一个局部规则冲突，并打开更高层权威压力。",
                "reveal_rule": "世界真相必须分批揭示。",
                "continuity_rule": "新地点、资产和权威压力必须被后续继承。",
            },
            "state_variables": [
                {
                    "key": "cross_system_understanding",
                    "variable_type": "knowledge",
                    "current_value": "只知道旧航线规则。",
                    "desired_direction": "逐步理解新帝国审计秩序。",
                    "change_triggers": ["破解航线记录", "公开审计听证"],
                    "failure_mode": "世界观退化为背景说明。",
                }
            ],
            "asset_ledger": [
                {
                    "key": "hidden_route_archive",
                    "asset_type": "information",
                    "value": "证明帝国篡改航线。",
                    "cost": "检索会留下审计记录。",
                    "exposure_risk": "审计庭会追踪异常访问。",
                    "attention_sources": ["帝国审计庭"],
                }
            ],
            "authority_claims": [
                {
                    "claimant": "帝国审计庭",
                    "target": "边境航线解释权",
                    "claim_basis": "帝国审计法",
                    "legitimacy": "公开合法但掩盖篡改。",
                    "conflict_with": ["边境导航员"],
                    "escalation_path": "从核查升级到封港。",
                }
            ],
            "scene_templates": [
                {
                    "key": "route-audit-hearing",
                    "template_name": "航线审计听证",
                    "use_case": "公开展示规则冲突和权威压力。",
                    "required_change": ["cross_system_understanding"],
                }
            ],
        },
        "structure_strategy": {
            "macro_strategy": "规则套利和权威追踪交替升级",
            "chapter_engine": "每章推进状态变量或资产风险",
            "pacing_rule": "短兑现与长债务交替",
            "freshness_rule": "连续两卷不得重复同一权威压力形态",
        },
        "plot_tree": [
            {
                "key": "mainline",
                "line_type": "main",
                "label": "边境航线记录",
                "role": "驱动外部目标",
                "current_state": "记录被篡改",
                "target_state": "找到篡改证据",
                "failure_if_removed": "故事失去规则套利主线",
            }
        ],
        "beat_schedule": [
            {
                "chapter_range": "1-5",
                "duty": "建立旧规则和新审计法的冲突",
                "state_change": "主角从旧知识持有者变成审计异常发现者",
                "payoff": "第一次破解局部航线异常",
                "hook_or_aftereffect": "审计庭注意到异常访问",
            }
        ],
        "change_vectors": ["世界理解变化", "资产风险变化", "权威压力变化"],
    }


def _healthy_volume_plan() -> list[dict[str, object]]:
    return [
        {
            "volume_number": 1,
            "primary_force_name": "港务官封锁",
            "active_authority_claims": ["港务官临时封港权"],
            "world_state_targets": ["cross_system_understanding +1"],
            "map_function": "灰港审计厅展示航线规则并制造港务官压力",
            "world_asset_refs": ["hidden_route_archive"],
            "asset_risk_escalation": "检索会留下审计记录",
            "reveal_budget": 1,
            "key_reveals": ["局部航线记录存在异常"],
        },
        {
            "volume_number": 2,
            "primary_force_name": "帝国审计庭",
            "active_authority_claims": ["帝国审计庭主张边境航线解释权"],
            "world_state_targets": ["cross_system_understanding +2"],
            "map_function": "听证会公开展示新旧规则冲突并引来审计庭",
            "world_asset_refs": ["hidden_route_archive"],
            "asset_risk_escalation": "审计庭会追踪异常访问并封存档案",
            "reveal_budget": 2,
            "key_reveals": ["帝国审计庭曾主动篡改边境航线"],
        },
    ]


def test_worldview_progression_gate_passes_escalating_volume_plan() -> None:
    report = evaluate_worldview_progression_gate(
        _story_design_kernel(),
        _healthy_volume_plan(),
    )

    assert report.passed is True
    assert report.blocking_findings == ()
    assert worldview_progression_report_to_dict(report)["passed"] is True


def test_worldview_progression_gate_flags_flat_authority_ladder() -> None:
    plan = _healthy_volume_plan()
    plan[1]["primary_force_name"] = "港务官封锁"
    plan[1]["active_authority_claims"] = ["港务官临时封港权"]
    plan[1]["asset_risk_escalation"] = "审计庭会追踪异常访问并封存档案"

    report = evaluate_worldview_progression_gate(_story_design_kernel(), plan)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "authority_ladder_flat" in codes


def test_worldview_progression_gate_flags_state_variable_stall() -> None:
    plan = _healthy_volume_plan()
    for volume in plan:
        volume["world_state_targets"] = []

    report = evaluate_worldview_progression_gate(_story_design_kernel(), plan)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "state_variable_stalls" in codes


def test_worldview_progression_gate_flags_map_function_missing() -> None:
    plan = _healthy_volume_plan()
    plan[0]["map_function"] = "灰港审计厅作为场景背景出现。"

    report = evaluate_worldview_progression_gate(_story_design_kernel(), plan)
    codes = {finding.code for finding in report.warnings}

    assert "map_function_missing" in codes


def test_worldview_progression_gate_flags_asset_risk_not_scaled() -> None:
    plan = _healthy_volume_plan()
    plan[1]["asset_risk_escalation"] = "检索会留下审计记录"

    report = evaluate_worldview_progression_gate(_story_design_kernel(), plan)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "asset_risk_not_scaled" in codes


def test_worldview_progression_gate_flags_reveal_distribution_imbalanced() -> None:
    plan = _healthy_volume_plan()
    plan[0]["reveal_budget"] = 4
    plan[0]["key_reveals"] = ["异常一", "异常二", "异常三", "异常四"]

    report = evaluate_worldview_progression_gate(_story_design_kernel(), plan)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "reveal_distribution_imbalanced" in codes
