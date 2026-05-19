# ruff: noqa: RUF001
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.planning_kernel import (
    build_prewrite_repair_directives,
    build_project_planning_kernel,
    evaluate_prewrite_readiness,
    persist_project_planning_kernel,
)
from bestseller.services.compliance_boundary_kernel import build_compliance_boundary_kernel_seed

pytestmark = pytest.mark.unit


def _project(**metadata):
    return SimpleNamespace(
        slug="xianxia-plan",
        title="道种试炼",
        genre="仙侠升级",
        sub_genre="宗门逆袭",
        target_chapters=120,
        metadata_json=metadata,
    )


def _book_spec() -> dict[str, object]:
    return {
        "series_engine": {
            "core_serial_engine": "低位修士用道种规则和资源账反制宗门压力。",
            "reader_promise": "每批章节兑现一次资源翻盘或规则发现。",
            "first_three_chapter_hook": "开局给出废灵根、道种异动和三月大考。",
            "chapter_ending_hook_strategy": "用资源、倒计时、敌人动作或道种异常收尾。",
            "payoff_rhythm": "3-6章小兑现，30章内大阶段兑现。",
        },
        "protagonist": {
            "name": "宁尘",
            "decision_policy": {"core_rule": "先保命，再换取可复用资源。"},
        },
    }


def _world_spec() -> dict[str, object]:
    return {
        "rules": [
            {"rule_name": "道种吸收", "description": "只能吸收残页余韵，过量会伤经脉。"}
        ],
        "factions": [
            {"name": "杂役峰", "goal": "维持配给秩序"},
            {"name": "丹房", "goal": "垄断筑基丹"},
        ],
        "power_system": {"realms": ["炼气", "筑基", "金丹"]},
    }


def _cast_spec() -> dict[str, object]:
    return {
        "protagonist": {
            "name": "宁尘",
            "decision_policy": {"core_rule": "先保命，再换取可复用资源。"},
        },
        "supporting_cast": [
            {"name": "苏瑶", "role": "rival", "relationship_to_protagonist": "互相试探"},
            {"name": "陆沉", "role": "broker", "relationship_to_protagonist": "资源交易"},
        ],
    }


def _volume_plan() -> list[dict[str, object]]:
    return [
        {
            "volume_number": 1,
            "conflict_phase": "survival",
            "primary_force_name": "杂役峰执事",
            "volume_climax": "大考前夜用残页规则反制栽赃。",
            "core_payoff": "低位反制",
            "foreshadowing_planted": ["废灵根旧案"],
        },
        {
            "volume_number": 2,
            "conflict_phase": "resource_war",
            "primary_force_name": "丹房管事",
            "volume_climax": "用灵草账本换取秘境名额。",
            "core_payoff": "资源翻盘",
            "foreshadowing_paid_off": ["废灵根旧案第一层"],
        },
        {
            "volume_number": 3,
            "conflict_phase": "faction_intrigue",
            "primary_force_name": "内门监察",
            "volume_climax": "以秘境阵眼暴露二十年前真相。",
            "core_payoff": "信息解谜",
            "reader_hook_to_next": "道种被内门古碑认出。",
        },
        {
            "volume_number": 4,
            "conflict_phase": "betrayal",
            "primary_force_name": "旧盟友陆沉",
            "volume_climax": "陆沉背叛逼宁尘公开一项新规则。",
            "core_payoff": "身份误判利用",
            "reader_hook_to_next": "祖师残页出现第二枚账印。",
        },
    ]


def _emotion_driven_kernel() -> dict[str, object]:
    return {
        "reader_emotion_promise": "让读者先理解宁尘为什么不敢退，再等待大考反击。",
        "primary_reader_waiting": ["大考反杀", "废灵根旧案"],
        "empathy_contracts": [
            {
                "contract_id": "empathy-opening",
                "character_key": "protagonist",
                "chapter_range": "1-3",
                "situation": "宁尘被杂役峰当成废灵根弃子。",
                "current_desire": "保住三月大考资格。",
                "fear_or_loss": "失去进入秘境的唯一机会。",
                "sensory_entry": "配给牌割进掌心的冷硬触感。",
                "judgment_logic": "先确认规则漏洞，再决定是否反击。",
                "reasonable_action": "假装认罚，实际记录配给账。",
                "consequence": "他保住资格，也被执事盯上。",
            }
        ],
        "bomb_contracts": [
            {
                "bomb_id": "bomb-exam-ledger",
                "bomb_type": "反杀炸弹",
                "chapter_range": "1-5",
                "reader_knows": "读者知道配给账能证明执事造假。",
                "character_blindspot": "执事不知道宁尘已经记下账目。",
                "danger": "执事会在大考前夜栽赃。",
                "trigger_condition": "大考前夜执事公开搜身。",
                "countdown": "五章内。",
                "consequence": "若失败，宁尘被逐出宗门。",
                "payoff_window": "第4-5章反杀。",
                "rational_ignorance": "宁尘一直表现得像认命。",
            }
        ],
        "ending_texture_contract": {
            "ending_type": "HE",
            "core_wish_fulfilled": "宁尘赢下大考资格。",
            "relationship_settlement": "苏瑶承认他不是废物。",
            "irreversible_cost_retained": "他和杂役峰旧日安稳彻底断裂。",
            "theme_answer": "弱者不是靠怨气翻身，而是靠看清规则代价。",
            "future_open": "秘境打开下一层规则。",
        },
    }


def _public_emotion_kernel() -> dict[str, object]:
    return {
        "target_segments": [
            {
                "id": "segment-low-position",
                "group_label": "被低位身份压住的升级读者",
                "life_context": "主角在宗门资源体系里没有解释权。",
                "public_emotion": "不甘、憋屈、想看旧判断被推翻。",
                "unsaid_sentence": "凭什么低位身份就没有资格？",
                "desired_compensation": "主角用本书道种规则拿回资源资格。",
            }
        ],
        "emotion_bridges": [
            {
                "bridge_id": "bridge-resource-rule",
                "source_segment_id": "segment-low-position",
                "bridge_type": "value_bridge",
                "public_anchor": "被身份和资源规则压住",
                "genre_translation": "转译为宗门资源账与道种规则的资格冲突。",
                "story_hook": "主角用道种规则证明旧资源判断失效。",
                "reader_payoff": "当众拿回资格并留下更高层规则债。",
                "title_hook": "旧账判我无资，我用道种翻案",
            }
        ],
        "forbidden_misreads": ["不能写成现实群体仇恨。"],
        "project_specificity_notes": "只绑定道种试炼项目。",
    }


def _story_design_kernel(*, enhanced: bool = True) -> dict[str, object]:
    worldview: dict[str, object] = {
        "premise": "宗门世界的资源、权力和成长都由信任债驱动。",
        "uniqueness_principle": "每次世界观使用都必须改变资源、关系或制度压力。",
        "invariants": [
            {
                "key": "trust_debt_accounting",
                "rule": "任何资源收益都会形成可追踪的信任债。",
                "violation_cost": "绕过信任债会导致关系破裂。",
                "narrative_use": "把升级爽点转化为后续关系和制度压力。",
            }
        ],
        "systems": [
            {
                "name": "信任债经营体系",
                "operating_logic": "资源产出取决于授权者与执行者之间的信任余额。",
                "resources_or_authority": "灵田、账册、授权令。",
                "limits": "短期压榨会透支后续产出。",
                "costs": "每次扩张都必须偿还关系或制度债务。",
                "failure_modes": ["信任挤兑"],
            }
        ],
        "factions": [],
        "locations": [],
        "reveal_ladder": [],
        "integration_contract": {
            "chapter_rule": "每章至少让一个世界规则通过选择、证据或代价落地。",
            "volume_rule": "每卷关闭一个局部规则冲突，并打开更高层级压力。",
            "reveal_rule": "世界真相必须分批揭示。",
            "continuity_rule": "新规则和代价必须被后续继承。",
        },
    }
    if enhanced:
        worldview.update(
            {
                "distilled_mechanism_bindings": [
                    {
                        "aggregate_key": "otherworld-cross-system",
                        "mechanism_id": "cross-system-rule-arbitrage",
                        "design_role": "world",
                        "source_confidence": 0.86,
                        "required_project_binding": "绑定到信任债经营体系。",
                        "state_variables": ["cross_system_understanding"],
                    }
                ],
                "state_variables": [
                    {
                        "key": "cross_system_understanding",
                        "variable_type": "knowledge",
                        "current_value": "只知道旧账法。",
                        "desired_direction": "理解宗门制度压力。",
                        "change_triggers": ["公开账册校验"],
                        "failure_mode": "世界观退化为背景说明。",
                    }
                ],
                "asset_ledger": [
                    {
                        "key": "spirit_field_account_book",
                        "asset_type": "ledger",
                        "value": "证明灵田产出和授权关系。",
                        "cost": "使用账册会留下账房校验记录。",
                        "exposure_risk": "长老会会追踪异常授权。",
                    }
                ],
                "authority_claims": [
                    {
                        "claimant": "宗门长老会",
                        "target": "灵田经营权",
                        "claim_basis": "宗门资源分配旧例",
                        "legitimacy": "公开合法但压制外门弟子。",
                        "escalation_path": "从试运营限制升级到执法堂封田。",
                    }
                ],
            }
        )
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
            "forbidden_defaults": [],
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
        "world_conflict_contracts": [],
        "worldview_kernel": worldview,
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


def test_prewrite_readiness_accepts_rich_planning_kernel() -> None:
    project = _project(
        story_facets={
            "setting": "外门杂役峰与三月秘境",
            "narrative_drive": "低位反制",
            "trope_tags": ["凡人流", "资源账", "宗门生存"],
        },
        benchmark_works=["凡人修仙传结构对标"],
    )

    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
    )
    report = evaluate_prewrite_readiness(kernel)

    assert report.passed is True
    assert report.score >= 80
    assert report.blocking_findings == ()
    assert report.capability_snapshot["benchmark_alignment"] is True
    assert report.capability_snapshot["volume_differentiation"] is True
    assert report.capability_snapshot["emotion_driven_core"] is False


def test_prewrite_readiness_blocks_thin_generic_planning() -> None:
    kernel = build_project_planning_kernel(
        _project(),
        book_spec={},
        world_spec={},
        cast_spec={},
        volume_plan=[],
    )
    report = evaluate_prewrite_readiness(kernel)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "benchmark_alignment_missing" in codes
    assert "unique_hook_missing" in codes
    assert "series_engine_missing" in codes


def test_prewrite_readiness_blocks_repeated_volume_pressure() -> None:
    project = _project(
        story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"},
        benchmark_works=["凡人修仙传结构对标"],
    )
    repeated = [
        {
            "volume_number": index,
            "conflict_phase": "survival",
            "primary_force_name": "丹房管事",
            "volume_climax": f"第{index}次丹房压迫",
            "core_payoff": f"资源翻盘{index}",
        }
        for index in range(1, 5)
    ]
    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=repeated,
    )
    report = evaluate_prewrite_readiness(kernel)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "volume_differentiation_missing" in codes
    directives = build_prewrite_repair_directives(report)
    assert any("更换主压力源" in item for item in directives)


def test_prewrite_readiness_blocks_unconsumed_distilled_strategy() -> None:
    project = _project(
        story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"},
        benchmark_works=["凡人修仙传结构对标"],
        distilled_strategy_card={
            "aggregate_key": "otherworld-cross-system",
            "maturity_score": 0.62,
            "maturity_status": "review",
            "source_count": 2,
            "selected_mechanisms": [
                {
                    "mechanism_id": "cross-system-rule-arbitrage",
                    "source_confidence": 0.86,
                    "design_role": "series_engine",
                    "adaptation_instruction": "转化为本项目因果链。",
                    "required_project_specific_binding": "绑定到道种规则。",
                    "failure_mode": "未绑定项目元素。",
                }
            ],
            "required_state_variables": ["cross_system_understanding"],
            "required_change_vectors": ["exploit_rule_gap"],
            "reader_reward_mix": ["knowledge_arbitrage"],
            "anti_copy_boundaries": ["exact-opening-chain"],
        },
    )
    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
    )
    report = evaluate_prewrite_readiness(kernel)
    codes = {finding.code for finding in report.blocking_findings}

    assert "distilled_strategy_not_consumed" in codes
    assert report.capability_snapshot["distilled_strategy_ready"] is False


def test_prewrite_readiness_accepts_valid_emotion_driven_kernel() -> None:
    project = _project(
        story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"},
        benchmark_works=["凡人修仙传结构对标"],
        emotion_driven_kernel=_emotion_driven_kernel(),
    )
    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
    )
    report = evaluate_prewrite_readiness(kernel)

    warning_codes = {finding.code for finding in report.warnings}
    assert "emotion_driven_kernel_missing" not in warning_codes
    assert report.capability_snapshot["emotion_driven_core"] is True


def test_prewrite_readiness_blocks_invalid_emotion_driven_kernel() -> None:
    broken_kernel = _emotion_driven_kernel()
    broken_kernel["empathy_contracts"] = [
        {
            "contract_id": "broken",
            "character_key": "protagonist",
            "chapter_range": "1",
            "situation": "宁尘被围堵。",
            "current_desire": "离开。",
        }
    ]
    project = _project(
        story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"},
        benchmark_works=["凡人修仙传结构对标"],
        emotion_driven_kernel=broken_kernel,
    )
    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
    )
    report = evaluate_prewrite_readiness(kernel)
    codes = {finding.code for finding in report.blocking_findings}

    assert "empathy_contract_missing" in codes
    assert report.capability_snapshot["emotion_driven_core"] is False


def test_prewrite_readiness_accepts_consumed_distilled_strategy() -> None:
    project = _project(
        story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"},
        benchmark_works=["凡人修仙传结构对标"],
        distilled_strategy_card={
            "aggregate_key": "otherworld-cross-system",
            "maturity_score": 0.62,
            "maturity_status": "review",
            "source_count": 2,
            "selected_mechanisms": [
                {
                    "mechanism_id": "cross-system-rule-arbitrage",
                    "source_confidence": 0.86,
                    "design_role": "series_engine",
                    "adaptation_instruction": "转化为本项目因果链。",
                    "required_project_specific_binding": "绑定到道种规则。",
                    "failure_mode": "未绑定项目元素。",
                }
            ],
            "required_state_variables": ["cross_system_understanding"],
            "required_change_vectors": ["exploit_rule_gap"],
            "reader_reward_mix": ["knowledge_arbitrage"],
            "anti_copy_boundaries": ["exact-opening-chain"],
        },
    )
    consumed_volume_plan = [
        {
            **volume,
            "distilled_state_delta": "cross_system_understanding -> exploit_rule_gap",
            "core_payoff": f"{volume.get('core_payoff', '')} / knowledge_arbitrage",
        }
        for volume in _volume_plan()
    ]

    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=consumed_volume_plan,
    )
    report = evaluate_prewrite_readiness(kernel)
    codes = {
        finding.code
        for finding in [*report.blocking_findings, *report.warnings]
    }

    assert "distilled_strategy_not_consumed" not in codes
    assert report.capability_snapshot["distilled_strategy_ready"] is True


def test_persist_planning_kernel_promotes_ranking_profile_to_metadata(tmp_path) -> None:
    profile = tmp_path / "xianxia-plan" / "story-bible" / "ranking-capability-profile.md"
    profile.parent.mkdir(parents=True)
    profile.write_text("# 榜单级能力 Profile\n\n- 每 5 章验证一次道种规则。", encoding="utf-8")
    project = _project(story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"})

    payload = persist_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
        output_base_dir=tmp_path,
    )

    assert payload["prewrite_readiness_report"]["passed"] is True
    assert payload["prewrite_repair_directives"] == []
    assert project.metadata_json["planning_kernel"]["benchmark"]["ranking_profile_present"] is True
    assert "道种规则" in project.metadata_json["ranking_capability_profile_block"]


def test_planning_kernel_tracks_enhanced_worldview_counts() -> None:
    project = _project(
        story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"},
        benchmark_works=["凡人修仙传结构对标"],
    )

    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
        story_design_kernel=_story_design_kernel(enhanced=True),
    )

    story_design = kernel["story_design"]
    assert story_design["worldview_state_variable_count"] == 1
    assert story_design["worldview_asset_count"] == 1
    assert story_design["worldview_authority_claim_count"] == 1
    assert story_design["worldview_distilled_binding_count"] == 1


def test_prewrite_readiness_warns_when_distilled_worldview_not_bound() -> None:
    project = _project(
        story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"},
        benchmark_works=["凡人修仙传结构对标"],
        distilled_strategy_card={
            "aggregate_key": "otherworld-cross-system",
            "maturity_score": 0.62,
            "maturity_status": "review",
            "source_count": 2,
            "selected_mechanisms": [
                {
                    "mechanism_id": "cross-system-rule-arbitrage",
                    "source_confidence": 0.86,
                    "design_role": "series_engine",
                    "adaptation_instruction": "转化为本项目因果链。",
                    "required_project_specific_binding": "绑定到道种规则。",
                    "failure_mode": "未绑定项目元素。",
                }
            ],
            "required_state_variables": ["cross_system_understanding"],
            "required_change_vectors": ["exploit_rule_gap"],
            "reader_reward_mix": ["knowledge_arbitrage"],
            "anti_copy_boundaries": ["exact-opening-chain"],
        },
    )
    consumed_volume_plan = [
        {
            **volume,
            "distilled_state_delta": "cross_system_understanding -> exploit_rule_gap",
            "core_payoff": f"{volume.get('core_payoff', '')} / knowledge_arbitrage",
        }
        for volume in _volume_plan()
    ]
    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=consumed_volume_plan,
        story_design_kernel=_story_design_kernel(enhanced=False),
    )
    report = evaluate_prewrite_readiness(kernel)
    warning_codes = {finding.code for finding in report.warnings}

    assert "distilled_worldview_not_bound" in warning_codes


def test_planning_kernel_tracks_public_emotion_and_compliance() -> None:
    project = _project(
        story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"},
        benchmark_works=["凡人修仙传结构对标"],
        public_emotion_kernel=_public_emotion_kernel(),
        compliance_boundary_kernel=build_compliance_boundary_kernel_seed(platform="番茄小说"),
    )

    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
    )
    report = evaluate_prewrite_readiness(kernel)

    assert kernel["public_emotion"]["present"] is True
    assert kernel["public_emotion"]["passed"] is True
    assert kernel["compliance_boundary"]["present"] is True
    assert kernel["compliance_boundary"]["passed"] is True
    assert report.capability_snapshot["public_emotion_core"] is True
    assert report.capability_snapshot["compliance_boundary"] is True


def test_prewrite_readiness_blocks_high_risk_compliance_boundary() -> None:
    risky_public_emotion = _public_emotion_kernel()
    risky_public_emotion["emotion_bridges"][0]["title_hook"] = (
        "被真实学校欺负后，我要报复现实所有人"
    )
    project = _project(
        story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"},
        benchmark_works=["凡人修仙传结构对标"],
        public_emotion_kernel=risky_public_emotion,
        compliance_boundary_kernel=build_compliance_boundary_kernel_seed(platform="番茄小说"),
    )

    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
    )
    report = evaluate_prewrite_readiness(kernel)
    codes = {finding.code for finding in report.blocking_findings}

    assert "compliance_boundary_high_risk" in codes
    assert report.capability_snapshot["compliance_boundary"] is False


def test_prewrite_readiness_scans_public_emotion_with_default_policy_pack() -> None:
    risky_public_emotion = _public_emotion_kernel()
    risky_public_emotion["emotion_bridges"][0]["title_hook"] = (
        "被真实学校欺负后，我要报复现实所有人"
    )
    project = _project(
        story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"},
        benchmark_works=["凡人修仙传结构对标"],
        public_emotion_kernel=risky_public_emotion,
    )

    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
    )
    report = evaluate_prewrite_readiness(kernel)
    warning_codes = {finding.code for finding in report.warnings}
    blocking_codes = {finding.code for finding in report.blocking_findings}

    assert "compliance_boundary_kernel_missing" in warning_codes
    assert "compliance_boundary_high_risk" in blocking_codes
