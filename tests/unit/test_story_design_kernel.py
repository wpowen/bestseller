# ruff: noqa: RUF001
from __future__ import annotations

from copy import deepcopy

from pydantic import ValidationError
import pytest

from bestseller.services.story_design_kernel import (
    StoryDesignKernel,
    render_story_design_kernel_prompt_block,
    story_design_kernel_from_dict,
    story_design_kernel_to_dict,
)

pytestmark = pytest.mark.unit


def _kernel_payload() -> dict[str, object]:
    return {
        "version": 1,
        "shape": {
            "length_class": "long",
            "publication_mode": "web_serial",
            "outline_depth": "chapter",
            "primary_duties": ["forward_pull", "relationship_state_shift"],
            "ending_contract": "close current loop while opening next desire",
        },
        "reader_promise": "每一章都让主角的选择改变关系、资源或局势。",
        "premise_contract": {
            "unique_hook": "修仙宗门的资源账由情感债驱动。",
            "core_question": "主角能否在不牺牲信任的前提下完成宗门扩张？",
            "commercial_pull": "升级、关系债、宗门经营三条线互相兑现。",
            "forbidden_defaults": ["家庭创伤或身世旧案作为默认动机", "神秘玉佩自动开挂"],
        },
        "character_conflict_contracts": [
            {
                "character_key": "protagonist",
                "external_goal": "拿下第一块灵田的经营权",
                "internal_need": "学会把盟友当合作者而不是资源",
                "pressure_source": "宗门长老要求短期收益",
                "choice_axis": "信任换效率，还是控制换安全",
                "change_vector": "从独断到分权",
            }
        ],
        "world_conflict_contracts": [
            {
                "axis": "资源规则",
                "rule": "灵田产出与照料者之间的信任度绑定",
                "visible_cost": "关系破裂会让灵田枯萎",
                "escalation_path": "个人信任扩展到宗门制度",
            }
        ],
        "worldview_kernel": {
            "premise": "宗门世界的资源、权力和成长都由可量化的信任债驱动。",
            "uniqueness_principle": "世界观必须让经营选择、关系选择和修炼选择共用同一套债务规则。",
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
            "macro_strategy": "卷内经营闭环，卷间扩大制度风险",
            "chapter_engine": "每章推进一个资源账或关系账",
            "pacing_rule": "短兑现与长债务交替",
            "freshness_rule": "连续三章不得重复同一压力源",
        },
        "plot_tree": [
            {
                "key": "mainline",
                "line_type": "main",
                "label": "灵田经营权主线",
                "role": "驱动外部目标",
                "current_state": "没有资源入口",
                "target_state": "建立第一处稳定产出",
                "failure_if_removed": "故事失去商业推进引擎",
            },
            {
                "key": "ally-trust",
                "line_type": "relationship",
                "label": "盟友信任线",
                "role": "制造选择代价",
                "current_state": "盟友只愿临时合作",
                "target_state": "形成可授权的协作关系",
                "dependency_on_mainline": "信任变化决定灵田经营是否稳定",
                "failure_if_removed": "主线变成单纯资源流水账",
            },
        ],
        "beat_schedule": [
            {
                "chapter_range": "1-3",
                "duty": "建立资源账与关系账",
                "state_change": "主角从拿不到灵田到获得试运营资格",
                "payoff": "读者看到经营规则第一次生效",
                "hook_or_aftereffect": "试运营资格绑定一个不可告人的盟友债务",
            }
        ],
        "change_vectors": ["资源权限变化", "信任边界变化", "制度压力变化"],
        "uniqueness_constraints": ["不得以亲人失踪作为默认驱动"],
        "reverse_outline_status": "not_started",
    }


def test_story_design_kernel_round_trips_and_renders_prompt_block() -> None:
    kernel = story_design_kernel_from_dict(_kernel_payload())

    assert isinstance(kernel, StoryDesignKernel)
    assert story_design_kernel_to_dict(kernel)["reader_promise"]

    block = render_story_design_kernel_prompt_block(kernel)

    assert "Story Design Kernel" in block
    assert "Reader promise" in block
    assert "Change vectors" in block
    assert "Worldview kernel" in block
    assert "trust_debt_accounting" in block
    assert "灵田经营权主线" in block


def test_story_design_kernel_accepts_story_principle_contracts() -> None:
    payload = deepcopy(_kernel_payload())
    payload.update(
        {
            "four_causes_contract": {
                "purpose_result": "让读者看到信任债从情感承诺变成经营结果。",
                "material_basis": ["灵田", "账册", "盟友旧债"],
                "formal_pattern": "莲花式主线下嵌套递进阶梯。",
                "driving_forces": ["读者期待兑现", "长老会压力", "盟友信任波动"],
                "proof_criteria": ["每个事件单元都改变一项资源或关系状态"],
            },
            "macro_structure_contract": {
                "structure_type": "progressive_staircase",
                "mainline_rule": "每个事件单元完成一次资源账到关系账的递进。",
                "subline_rule": "盟友信任线只在主线选择产生代价时推进。",
                "anti_homogeneity_rule": "事件六步跨章节分布，不要求每章完整重复。",
            },
            "reader_desire_matrix": [
                {
                    "desire_type": "respect_value",
                    "reader_expectation": "期待主角用可信任的经营方式赢过短期压榨。",
                    "payoff_mode": "阶段性资源兑现后暴露更高层代价。",
                    "risk_control": "避免每章都用同一种阻碍和同一种尾钩。",
                }
            ],
            "event_pattern_inventory": [
                {
                    "pattern_type": "obstacle_escalation",
                    "use_case": "在事件单元中段提高外部压力。",
                    "reader_effect": "制造解决欲而不是重复开局刺激。",
                    "anti_repetition_rule": "连续事件单元不得复用同一阻碍来源。",
                }
            ],
        }
    )

    kernel = story_design_kernel_from_dict(payload)
    dumped = story_design_kernel_to_dict(kernel)
    block = render_story_design_kernel_prompt_block(kernel)

    assert (
        dumped["four_causes_contract"]["purpose_result"]
        == "让读者看到信任债从情感承诺变成经营结果。"
    )
    assert dumped["macro_structure_contract"]["structure_type"] == "progressive_staircase"
    assert dumped["reader_desire_matrix"][0]["desire_type"] == "respect_value"
    assert "Four causes contract" in block
    assert "progressive_staircase" in block
    assert "respect_value" in block
    assert "事件六步跨章节分布" in block


def test_subplots_must_depend_on_mainline() -> None:
    payload = deepcopy(_kernel_payload())
    plot_tree = payload["plot_tree"]
    assert isinstance(plot_tree, list)
    subplot = plot_tree[1]
    assert isinstance(subplot, dict)
    subplot["dependency_on_mainline"] = ""

    with pytest.raises(ValidationError):
        story_design_kernel_from_dict(payload)


def test_kernel_requires_at_least_one_main_plot_line() -> None:
    payload = deepcopy(_kernel_payload())
    plot_tree = payload["plot_tree"]
    assert isinstance(plot_tree, list)
    for node in plot_tree:
        assert isinstance(node, dict)
        node["line_type"] = "subplot"

    with pytest.raises(ValidationError):
        story_design_kernel_from_dict(payload)


def test_worldview_kernel_requires_operational_invariants() -> None:
    payload = deepcopy(_kernel_payload())
    worldview = payload["worldview_kernel"]
    assert isinstance(worldview, dict)
    worldview["invariants"] = []

    with pytest.raises(ValidationError):
        story_design_kernel_from_dict(payload)


def test_worldview_kernel_accepts_distilled_operational_contracts() -> None:
    payload = deepcopy(_kernel_payload())
    worldview = payload["worldview_kernel"]
    assert isinstance(worldview, dict)
    worldview.update(
        {
            "distilled_mechanism_bindings": [
                {
                    "aggregate_key": "otherworld-cross-system",
                    "mechanism_id": "cross-system-rule-arbitrage",
                    "design_role": "world_pressure",
                    "source_confidence": 0.86,
                    "required_project_binding": "绑定到本书的信任债规则套利。",
                    "state_variables": [
                        "cross_system_understanding",
                        "higher_authority_attention",
                    ],
                    "required_cost": "每次套利都会提高长老会关注度。",
                    "anti_copy_boundaries": ["specific_family_inheritance_murder"],
                }
            ],
            "state_variables": [
                {
                    "key": "higher_authority_attention",
                    "variable_type": "counter",
                    "current_value": "0",
                    "desired_direction": "increase_with_visible_rule_breaks",
                    "change_triggers": ["公开利用信任债规则套利"],
                    "failure_mode": "套利只给收益不带来更高层压力。",
                    "source_mechanism_ids": ["cross-system-rule-arbitrage"],
                }
            ],
            "asset_ledger": [
                {
                    "key": "trust_debt_ledger",
                    "asset_type": "world_rule_asset",
                    "value": "证明资源收益和信任债可互相换算。",
                    "cost": "每次使用都暴露主角掌握旧制度。",
                    "exposure_risk": "长老会开始审计账册来源。",
                    "attention_sources": ["宗门长老会"],
                    "source_mechanism_ids": ["asset-value-attracts-risk"],
                }
            ],
            "authority_claims": [
                {
                    "claimant": "宗门长老会",
                    "target": "外门灵田规则解释权",
                    "claim_basis": "宗门旧制和批文权。",
                    "legitimacy": "公开合法但动机偏私。",
                    "conflict_with": ["主角的信任债账本"],
                    "escalation_path": "从账册审查升级为制度审判。",
                }
            ],
            "scene_templates": [
                {
                    "key": "social-venue-world-display",
                    "template_name": "社会场景展示规则",
                    "use_case": "在灵田交割现场展示规则并制造关系压力。",
                    "required_change": ["resource", "faction_pressure"],
                    "source_mechanism_ids": ["social-venue-world-display"],
                }
            ],
            "anti_copy_boundaries": ["specific_family_inheritance_murder"],
        }
    )

    kernel = story_design_kernel_from_dict(payload)
    serialized = story_design_kernel_to_dict(kernel)
    serialized_worldview = serialized["worldview_kernel"]

    assert serialized_worldview["state_variables"][0]["key"] == (
        "higher_authority_attention"
    )

    block = render_story_design_kernel_prompt_block(kernel)

    assert "higher_authority_attention" in block
    assert "cross-system-rule-arbitrage" in block
    assert "trust_debt_ledger" in block
    assert "宗门长老会 -> 外门灵田规则解释权" in block
    assert "specific_family_inheritance_murder" in block


def test_story_design_kernel_normalizes_common_llm_aliases() -> None:
    payload = deepcopy(_kernel_payload())
    payload["reader_promise"] = {
        "core_promises": ["每章推进一条可验证线索", "终章关闭当前故事"],
        "forbidden_violations": ["不得留核心谜题到书外解决"],
    }
    payload["character_conflict_contracts"] = [
        {
            "character_key": "protagonist",
            "external_goal": "30章内关闭死亡倒计时。",
            "internal_need": "学会把调查风险分给可信盟友。",
            "pressure_trigger": "母亲失忆真相可能是自愿选择。",
            "choice_axis": "独自保密还是共享线索。",
            "payoff_mode": "付出记忆代价后仍选择信任盟友。",
        }
    ]
    worldview = payload["worldview_kernel"]
    assert isinstance(worldview, dict)
    worldview.update(
        {
            "reveal_ladder": [
                {
                    "stage": "规则显形期",
                    "chapter_range": "1-10",
                    "reveals": ["死亡预言启动", "划名消失规则被验证"],
                    "unlock_condition": "通过具体证据和代价揭示。",
                    "reader_requirement": "读者能理解基本规则。",
                }
            ],
            "asset_ledger": [
                {
                    "id": "asset_001",
                    "name": "巡夜录",
                    "strategic_value": "记录死亡倒计时与巡夜人真相。",
                    "cost_to_obtain": "必须完成高风险修复任务。",
                    "exposure": "被馆长发现会失去访问权。",
                },
                {
                    "asset_key": "修复技术资格",
                    "asset_type": "skill",
                    "visible_cost": "每次修复都在喂养档案馆。",
                    "exposure": "会被馆长监视。",
                }
            ],
            "authority_claims": [
                {
                    "key": "claim_01",
                    "entity": "顾砚",
                    "claim": "修复师资格允许他接触普通档案。",
                    "authority_type": "formal",
                    "legitimacy_source": "修复师协会认证。",
                    "scope": "普通档案修复。",
                    "current_status": "暂时有效。",
                    "challenge_condition": "协会可随时撤销资格。",
                },
                {
                    "authority_key": "管理层授权",
                    "claim_type": "formal_authority",
                    "legitimacy_source": "馆长行政命令。",
                    "scope": "档案馆公共区域访问。",
                    "limits": "不包含深层区域。",
                    "risk": "该授权可能被反派撤销。",
                }
            ],
            "scene_templates": [
                {
                    "scene_id": "template_rule_activation",
                    "purpose": "顾砚修复被烧毁档案时释放新规则。",
                    "required_elements": ["修复动作", "规则显影"],
                    "expected_outcome": "释放一条新规则。",
                },
                {
                    "template_key": "规则验证场景",
                    "trigger_condition": "主角发现疑似规则现象时",
                    "scene_structure": ["观察现象", "收集证据", "评估风险"],
                    "cost_requirement": "必须付出可见代价。",
                },
                {
                    "template_key": "线索推进场景",
                    "structure": "现有线索指向→新证据出现→线索指向改变",
                    "variation": "线索可以通过修复档案或角色对话推进。",
                    "obligation": "每2-3章必须推进一条完整线索。",
                }
            ],
            "state_variables": [
                {
                    "key": "death_countdown",
                    "variable_type": "counter",
                    "current_value": 30,
                    "desired_direction": "decrease",
                    "change_triggers": "修复一页档案。",
                    "failure_mode": "倒计时不产生压力。",
                }
            ],
        }
    )
    structure_strategy = payload["structure_strategy"]
    assert isinstance(structure_strategy, dict)
    structure_strategy["chapter_engine"] = {
        "hook_types": ["规则威胁钩"],
        "rotation_rule": "连续章节不得重复同一压力源。",
    }
    structure_strategy["pacing_rule"] = {
        "clue_frequency": "每2-3章一个新线索。",
        "real_solution": "第25章后验证终局解法。",
    }
    payload["plot_tree"].append(
        {
            "key": "antagonist-pressure",
            "line_type": "antagonist",
            "label": "馆长续命线",
            "role": "制造外部压迫",
            "current_state": "隐藏真实动机",
            "target_state": "被迫暴露续命计划",
            "dependency_on_mainline": "死亡倒计时调查会逐步逼出馆长计划。",
            "failure_if_removed": "主线缺少对抗压力。",
        }
    )
    payload["plot_tree"].append(
        {
            "key": "mother-truth",
            "line_type": "main_emotional",
            "label": "母亲真相线",
            "role": "提供情感核心",
            "current_state": "失忆原因不明",
            "target_state": "第20章完成真相揭露",
            "failure_if_removed": "故事失去情感代价。",
        }
    )
    payload["beat_schedule"] = [
        {
            "chapter_range": "1-3",
            "duty": "启动死亡预言和规则验证。",
            "state_changes": ["倒计时启动", "第一条规则被观察"],
            "payoff": "读者看到规则第一次生效。",
            "hook_or_aftereffect": "发现死亡预言与巡夜录关联。",
        }
    ]
    payload["change_vectors"] = [
        {"vector": "线索从死亡记录转向巡夜人真相"},
        {"change_vector": "母子关系从保护变成共同承担"},
    ]
    payload["uniqueness_constraints"] = [
        {"constraint": "不得把梦境作为谜题解法", "implementation": "所有真相来自证据链"},
        "每章必须产生可见状态变化",
    ]
    payload.pop("shape")

    kernel = story_design_kernel_from_dict(payload)
    serialized = story_design_kernel_to_dict(kernel)
    worldview_out = serialized["worldview_kernel"]

    assert serialized["shape"]["length_class"] == "novella"
    assert "每章推进一条可验证线索" in serialized["reader_promise"]
    assert serialized["character_conflict_contracts"][0]["pressure_source"] == (
        "母亲失忆真相可能是自愿选择。"
    )
    assert serialized["character_conflict_contracts"][0]["change_vector"] == (
        "付出记忆代价后仍选择信任盟友。"
    )
    assert "划名消失规则被验证" in worldview_out["reveal_ladder"][0]["reveal"]
    assert worldview_out["state_variables"][0]["current_value"] == "30"
    assert worldview_out["state_variables"][0]["change_triggers"] == ["修复一页档案。"]
    assert worldview_out["asset_ledger"][0]["key"] == "asset_001"
    assert worldview_out["asset_ledger"][0]["asset_type"] == "巡夜录"
    assert worldview_out["asset_ledger"][0]["value"] == "记录死亡倒计时与巡夜人真相。"
    assert worldview_out["asset_ledger"][1]["value"] == "修复技术资格"
    assert worldview_out["asset_ledger"][1]["cost"] == "每次修复都在喂养档案馆。"
    assert worldview_out["authority_claims"][0]["claimant"] == "顾砚"
    assert worldview_out["authority_claims"][1]["claimant"] == "管理层授权"
    assert worldview_out["scene_templates"][0]["key"] == "template_rule_activation"
    assert "观察现象" in worldview_out["scene_templates"][1]["required_change"][0]
    assert worldview_out["scene_templates"][2]["use_case"].startswith("现有线索指向")
    assert "规则威胁钩" in serialized["structure_strategy"]["chapter_engine"]
    assert serialized["plot_tree"][-2]["line_type"] == "subplot"
    assert serialized["plot_tree"][-1]["line_type"] == "main"
    assert serialized["beat_schedule"][0]["state_change"] == "倒计时启动；第一条规则被观察"
    assert serialized["change_vectors"] == [
        "线索从死亡记录转向巡夜人真相",
        "母子关系从保护变成共同承担",
    ]
    assert serialized["uniqueness_constraints"] == [
        "不得把梦境作为谜题解法",
        "每章必须产生可见状态变化",
    ]


def test_story_design_kernel_normalizes_live_planner_schema_drift() -> None:
    payload = deepcopy(_kernel_payload())
    worldview = payload["worldview_kernel"]
    assert isinstance(worldview, dict)
    worldview["distilled_mechanism_bindings"] = [
        {
            "mechanism_key": "行为语义分析",
            "binding_type": "protagonist_capability",
            "description": "通过微表情、肢体语言和决策模式推导真实意图。",
            "binding_detail": "每次使用能力都必须暴露主角的推理边界和对手反制空间。",
            "source_confidence": "高——设定明确，逻辑自洽。",
        }
    ]
    worldview["state_variables"] = [
        {
            "key": "truth_visibility",
            "current_value": "观众只能看到表层证据",
            "change_triggers": [],
        },
        {
            "name": "risk_exposure",
            "description": "主角分析方式被对手学习的风险",
        },
    ]
    worldview["anti_copy_boundaries"] = [
        {
            "boundary_key": "禁止复用样本书专名链路",
            "rule": "本书只能使用原创角色、规则和线索表达。",
        }
    ]
    plot_tree = payload["plot_tree"]
    assert isinstance(plot_tree, list)
    plot_tree.append(
        {
            "key": "case-origin",
            "line_type": "backstory",
            "label": "旧案真相线",
            "role": "解释死亡游戏的规则来源",
            "current_state": "只露出异常痕迹",
            "target_state": "终章形成可验证真相",
            "dependency_on_mainline": "旧案真相必须通过当前死亡游戏的证据链推进。",
            "failure_if_removed": "谜题会只剩规则展示，缺少终局解释。",
        }
    )

    kernel = story_design_kernel_from_dict(payload)
    serialized = story_design_kernel_to_dict(kernel)
    serialized_worldview = serialized["worldview_kernel"]

    assert serialized["plot_tree"][-1]["line_type"] == "mystery"
    binding = serialized_worldview["distilled_mechanism_bindings"][0]
    assert binding["aggregate_key"] == "行为语义分析"
    assert binding["mechanism_id"] == "行为语义分析"
    assert binding["source_confidence"] == 0.7
    assert binding["design_role"] == "protagonist_capability"
    assert "反制空间" in binding["required_project_binding"]
    variables = serialized_worldview["state_variables"]
    assert variables[0]["variable_type"] == "information"
    assert variables[0]["change_triggers"]
    assert variables[0]["failure_mode"]
    assert variables[1]["key"] == "risk_exposure"
    assert variables[1]["variable_type"] == "risk"
    assert "禁止复用样本书专名链路" in serialized_worldview["anti_copy_boundaries"][0]
