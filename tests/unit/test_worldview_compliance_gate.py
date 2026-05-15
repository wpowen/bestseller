# ruff: noqa: RUF001
from __future__ import annotations

from copy import deepcopy

import pytest

from bestseller.services.worldview_compliance_gate import (
    evaluate_worldview_compliance_gate,
    worldview_compliance_report_to_dict,
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
        "reader_promise": "每章都让资源、关系或制度位置发生可见变化。",
        "premise_contract": {
            "unique_hook": "灵田产出与信任债绑定",
            "core_question": "主角能否把个人信任扩展成宗门制度?",
            "commercial_pull": "经营成果、关系债和规则漏洞互相兑现。",
            "forbidden_defaults": ["父母失踪", "神秘玉佩"],
        },
        "character_conflict_contracts": [
            {
                "character_key": "protagonist",
                "external_goal": "获得第一块灵田经营权",
                "internal_need": "学会授权",
                "pressure_source": "宗门收益考核",
                "choice_axis": "控制还是信任",
                "change_vector": "资源权限变化",
            }
        ],
        "world_conflict_contracts": [
            {
                "axis": "灵田规则",
                "rule": "信任债影响灵田产出",
                "visible_cost": "关系破裂会让产出下降",
                "escalation_path": "从个人关系扩展到宗门制度",
            }
        ],
        "worldview_kernel": {
            "premise": "宗门世界的资源、权力和成长都由可量化的信任债驱动。",
            "uniqueness_principle": "经营选择、关系选择和修炼选择共用同一套债务规则。",
            "invariants": [
                {
                    "key": "trust_debt_accounting",
                    "rule": "任何资源收益都会形成可追踪的信任债。",
                    "violation_cost": "绕过信任债会导致灵田产出衰败或盟友关系破裂。",
                    "narrative_use": "把升级爽点转化为后续关系和制度压力。",
                }
            ],
            "systems": [
                {
                    "name": "信任债经营体系",
                    "operating_logic": "资源产出取决于授权者与执行者之间的信任余额。",
                    "resources_or_authority": "灵田、账册、授权令、盟友信用。",
                    "limits": "短期压榨可以提速，但会透支后续产出。",
                    "costs": "每次扩张都必须偿还一个关系或制度债务。",
                    "failure_modes": ["信任挤兑", "账册被篡改"],
                }
            ],
            "factions": [
                {
                    "name": "宗门长老会",
                    "public_role": "管理宗门资源分配。",
                    "hidden_agenda": "用短期收益证明主角路线不可持续。",
                    "resources": "灵田批文、执法堂、账房。",
                    "pressure_on_protagonist": "逼主角用控制替代信任。",
                }
            ],
            "locations": [
                {
                    "name": "外门灵田",
                    "surface_function": "低阶弟子生产资源的场所。",
                    "hidden_function": "测试信任债规则是否成立的第一块实验田。",
                    "conflict_sources": ["产出考核", "水源争夺"],
                    "evidence_or_resource_types": ["灵田账册", "灌溉令牌"],
                }
            ],
            "reveal_ladder": [
                {
                    "stage": "volume-1",
                    "reveal": "信任债不是主角发明，而是宗门早已废弃的旧制度。",
                    "earliest_chapter": 8,
                    "unlock_condition": "第一处灵田经营失败或险胜后才能揭示。",
                }
            ],
            "integration_contract": {
                "chapter_rule": "每章至少让一个世界规则通过选择、证据或代价落地。",
                "volume_rule": "每卷关闭一个局部规则冲突，并打开更高层级的制度冲突。",
                "reveal_rule": "未到揭示点的世界真相只能通过物件或异常暗示。",
                "continuity_rule": "所有新地点、势力和规则必须回写到世界观账本。",
            },
        },
        "structure_strategy": {
            "macro_strategy": "经营闭环逐步扩大",
            "chapter_engine": "每章推进一个资源账或关系账",
            "pacing_rule": "短兑现与长债务交替",
            "freshness_rule": "连续三章不得重复同一压力源",
        },
        "plot_tree": [
            {
                "key": "mainline",
                "line_type": "main",
                "label": "灵田经营权",
                "role": "驱动外部目标",
                "current_state": "没有资源入口",
                "target_state": "稳定产出",
                "failure_if_removed": "故事失去经营推进",
            }
        ],
        "beat_schedule": [
            {
                "chapter_range": "1-3",
                "duty": "建立资源账",
                "state_change": "从无资格到获得试运营资格",
                "payoff": "经营规则第一次兑现",
                "hook_or_aftereffect": "资格绑定隐藏债务",
            }
        ],
        "change_vectors": ["资源权限变化", "信任边界变化", "制度压力变化"],
    }


def _outline() -> dict[str, object]:
    return {
        "batch_name": "volume-1-outline",
        "volume_number": 1,
        "chapters": [
            {
                "chapter_number": 1,
                "title": "灵田试约",
                "goal": "主角争取灵田试运营资格，让资源权限从无到有。",
                "world_rule_refs": ["trust_debt_accounting"],
                "world_rule_landing": "主角用账册证明信任债会影响三日内的灵田产出。",
                "location_refs": ["外门灵田"],
                "faction_refs": ["宗门长老会"],
                "key_reveals": ["信任债会改变灵田产出，但旧制度来源仍被遮住。"],
            }
        ],
    }


def _enhanced_story_design_kernel() -> dict[str, object]:
    kernel = deepcopy(_story_design_kernel())
    worldview = kernel["worldview_kernel"]
    assert isinstance(worldview, dict)
    worldview["state_variables"] = [
        {
            "key": "trust_balance",
            "variable_type": "relationship_resource",
            "current_value": "主角没有可信授权记录。",
            "desired_direction": "逐步建立可公开验证的信任余额。",
            "change_triggers": ["公开授权", "账册校验"],
            "failure_mode": "经营爽点变成无成本开挂。",
            "source_mechanism_ids": ["trust-debt-accounting"],
        }
    ]
    worldview["asset_ledger"] = [
        {
            "key": "spirit_field_account_book",
            "asset_type": "ledger",
            "value": "证明灵田产出和授权关系的账册。",
            "cost": "使用账册会留下账房校验记录。",
            "exposure_risk": "长老会会追踪异常授权。",
            "attention_sources": ["宗门长老会"],
        }
    ]
    worldview["authority_claims"] = [
        {
            "claimant": "宗门长老会",
            "target": "灵田经营权",
            "claim_basis": "宗门资源分配旧例",
            "legitimacy": "公开合法但压制外门弟子。",
            "conflict_with": ["主角的信任债经营体系"],
            "escalation_path": "从试运营限制升级到执法堂封田。",
        }
    ]
    worldview["scene_templates"] = [
        {
            "key": "public-rule-audit",
            "template_name": "公开规则审计",
            "use_case": "让世界规则在公开场合产生代价。",
            "required_change": ["trust_balance"],
            "source_mechanism_ids": ["trust-debt-accounting"],
        }
    ]
    worldview["anti_copy_boundaries"] = ["退婚羞辱"]
    return kernel


def _enhanced_outline() -> dict[str, object]:
    outline = deepcopy(_outline())
    chapter = outline["chapters"][0]
    assert isinstance(chapter, dict)
    chapter["world_rule_landing"] = (
        "主角用账册证明信任债会影响三日内的灵田产出，"
        "使用账册会留下账房校验记录，并让长老会会追踪异常授权。"
    )
    chapter["world_state_deltas"] = [
        {
            "key": "trust_balance",
            "delta": "+1",
            "evidence": "主角公开授权并通过账册校验。",
        }
    ]
    chapter["world_asset_refs"] = ["spirit_field_account_book"]
    chapter["authority_claim_refs"] = ["灵田经营权"]
    chapter["world_scene_template_ref"] = "public-rule-audit"
    chapter["reveal_weight"] = 1
    return outline


def test_worldview_compliance_gate_passes_grounded_outline() -> None:
    report = evaluate_worldview_compliance_gate(_story_design_kernel(), _outline())

    assert report.passed is True
    assert report.blocking_findings == ()
    assert worldview_compliance_report_to_dict(report)["passed"] is True


def test_worldview_compliance_gate_flags_chapter_without_world_rule_landing() -> None:
    outline = deepcopy(_outline())
    chapter = outline["chapters"][0]
    assert isinstance(chapter, dict)
    chapter.pop("world_rule_refs")
    chapter.pop("world_rule_landing")

    report = evaluate_worldview_compliance_gate(_story_design_kernel(), outline)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "world_rule_not_grounded" in codes


def test_worldview_compliance_gate_flags_future_reveal_leak() -> None:
    outline = deepcopy(_outline())
    chapter = outline["chapters"][0]
    assert isinstance(chapter, dict)
    chapter["key_reveals"] = ["信任债不是主角发明，而是宗门早已废弃的旧制度。"]

    report = evaluate_worldview_compliance_gate(_story_design_kernel(), outline)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "world_reveal_leak" in codes


def test_worldview_compliance_gate_flags_unregistered_location_and_faction() -> None:
    outline = deepcopy(_outline())
    chapter = outline["chapters"][0]
    assert isinstance(chapter, dict)
    chapter["location_refs"] = ["黑水码头"]
    chapter["faction_refs"] = ["盐帮"]

    report = evaluate_worldview_compliance_gate(_story_design_kernel(), outline)
    codes = {finding.code for finding in report.warnings}

    assert report.passed is True
    assert "unregistered_world_location" in codes
    assert "unregistered_world_faction" in codes


def test_worldview_gate_flags_missing_state_delta() -> None:
    outline = _enhanced_outline()
    chapter = outline["chapters"][0]
    assert isinstance(chapter, dict)
    chapter.pop("world_state_deltas")

    report = evaluate_worldview_compliance_gate(_enhanced_story_design_kernel(), outline)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "world_state_delta_missing" in codes


def test_worldview_gate_flags_unregistered_state_variable() -> None:
    outline = _enhanced_outline()
    chapter = outline["chapters"][0]
    assert isinstance(chapter, dict)
    chapter["world_state_deltas"] = [
        {"key": "untracked_pressure", "delta": "+1", "evidence": "临时新增压力。"}
    ]

    report = evaluate_worldview_compliance_gate(_enhanced_story_design_kernel(), outline)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "unregistered_world_state_variable" in codes


def test_worldview_gate_requires_asset_cost_or_exposure() -> None:
    outline = _enhanced_outline()
    chapter = outline["chapters"][0]
    assert isinstance(chapter, dict)
    chapter["world_rule_landing"] = "主角用账册证明信任债会影响三日内的灵田产出。"
    chapter["world_state_deltas"] = [
        {"key": "trust_balance", "delta": "+1", "evidence": "主角公开授权。"}
    ]

    report = evaluate_worldview_compliance_gate(_enhanced_story_design_kernel(), outline)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "world_asset_cost_missing" in codes


def test_worldview_gate_flags_reveal_budget_exceeded() -> None:
    outline = _enhanced_outline()
    chapter = outline["chapters"][0]
    assert isinstance(chapter, dict)
    chapter["reveal_weight"] = 3

    report = evaluate_worldview_compliance_gate(_enhanced_story_design_kernel(), outline)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "world_reveal_budget_exceeded" in codes


def test_worldview_gate_flags_anti_copy_boundary_hit() -> None:
    outline = _enhanced_outline()
    chapter = outline["chapters"][0]
    assert isinstance(chapter, dict)
    chapter["title"] = "退婚羞辱后的灵田试约"

    report = evaluate_worldview_compliance_gate(_enhanced_story_design_kernel(), outline)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "world_anti_copy_boundary_hit" in codes
