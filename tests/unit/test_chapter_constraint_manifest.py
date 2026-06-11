from __future__ import annotations

import pytest

from bestseller.services.canon_guardrails import (
    CanonForbiddenTerm,
    CanonGuardrails,
    CanonStateRule,
)
from bestseller.services.chapter_constraint_manifest import (
    PrewritePlan,
    build_safe_prewrite_plan,
    compile_chapter_constraint_manifest,
    normalize_prewrite_plan_for_manifest,
    parse_prewrite_plan,
    render_constraint_manifest_block,
    render_prewrite_plan_block,
    render_prewrite_plan_prompt,
    validate_chapter_prose_for_promotion,
    validate_prewrite_plan,
)
from bestseller.services.prompt_packs import get_prompt_pack

pytestmark = pytest.mark.unit


def test_compile_manifest_turns_scene_context_into_contract() -> None:
    guardrails = CanonGuardrails(
        forbidden_terms=(CanonForbiddenTerm(term="十七年前", reason="非法时间锚"),),
        state_rules=(
            CanonStateRule(
                subject="裴镜渊",
                status="第21章前不得真人出场",
                applies_after_chapter=20,
                forbidden_patterns=("裴镜渊.{0,20}走进",),
            ),
        ),
    )

    manifest = compile_chapter_constraint_manifest(
        chapter_number=2,
        scene_number=1,
        participants=["林渊", "王建业"],
        scene_time_label="今夜",
        scene_metadata={"location_name": "十七栋 23 层"},
        scene_exit_state={"王建业": "仍被押在走廊"},
        story_bible_context={
            "allowed_time_anchors": ["三年前", "三十年前"],
            "protagonist": {"abilities": ["阴阳眼", "罗盘"]},
            "protagonist_forbidden_vocabulary": ["破案"],
        },
        hook_requirement="倒计时",
        canon_guardrails=guardrails,
    )

    assert manifest.allowed_characters == ["林渊", "王建业"]
    assert "裴镜渊" in manifest.characters_must_not_appear
    assert "十七年前" in manifest.forbidden_terms
    assert manifest.allowed_time_anchors == ["今夜", "三年前", "三十年前"]
    assert manifest.allowed_locations == ["十七栋 23 层"]
    assert manifest.must_use_protagonist_abilities == ["阴阳眼", "罗盘"]
    assert manifest.must_avoid_protagonist_vocabulary == ["破案"]
    assert manifest.must_echo_hooks_from_prev == ["倒计时"]


def test_validate_prewrite_plan_rejects_forbidden_character_and_time() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=2,
        participants=["林渊", "王建业"],
        scene_time_label="今夜",
        canon_guardrails=CanonGuardrails(
            state_rules=(
                CanonStateRule(
                    subject="裴镜渊",
                    applies_after_chapter=20,
                    forbidden_patterns=("裴镜渊",),
                ),
            )
        ),
        project_metadata={"forbidden_time_anchors": ["十七年前"]},
    )
    plan = PrewritePlan(
        characters_to_use=["林渊", "裴镜渊"],
        time_anchors_to_use=["十七年前"],
    )

    result = validate_prewrite_plan(plan, manifest)

    assert result.passed is False
    assert any("裴镜渊" in item for item in result.violations)
    assert any("十七年前" in item for item in result.violations)


def test_validate_prewrite_plan_allows_relative_time_expressions() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        participants=["林渊", "王建业"],
        scene_time_label="23:43",
    )
    plan = PrewritePlan(
        characters_to_use=["林渊"],
        time_anchors_to_use=["23:43", "十几秒后"],
        ending_hook_type="新变量",
        ending_modes_to_avoid=["总结主题", "作者式预告", "硬转下一章", "口号式收束"],
    )

    result = validate_prewrite_plan(plan, manifest)

    assert result.passed is True


def test_off_screen_only_character_is_not_hard_banned_in_prose() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        participants=["林渊"],
        project_metadata={"characters_off_screen_only": ["张建军"]},
    )

    result = validate_chapter_prose_for_promotion(
        "门外响起三短一长的敲门声，张建军的声音隔着门板发颤。",
        manifest,
    )

    assert result.passed is True


def test_safe_plan_satisfies_manifest() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        participants=["林渊", "孙九斤"],
        scene_time_label="今夜",
        scene_metadata={"location": "城南旧事馆"},
        story_bible_context={"protagonist_forbidden_vocabulary": ["立案"]},
    )

    plan = build_safe_prewrite_plan(manifest)

    assert validate_prewrite_plan(plan, manifest).passed is True
    assert plan.characters_to_use == ["林渊", "孙九斤"]
    assert plan.time_anchors_to_use == ["今夜"]
    assert plan.locations_to_use == ["城南旧事馆"]
    assert plan.vocabulary_to_avoid == ["立案"]


def test_chapter_prose_promotion_does_not_require_manual_status() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        participants=["林渊", "王建业"],
        project_metadata={
            "protagonist_forbidden_vocabulary": ["破案"],
            "body_object_state_contract": {
                "tracked_objects": {"康熙铜钱": "必须说明位置"},
                "require_visible_cause_for_bleeding": True,
            },
            "ending_hook_contract": {
                "required_hook_target": "第八张脸",
                "forbidden_ending_modes": ["总结主题"],
            },
        },
    )

    result = validate_chapter_prose_for_promotion(
        "林渊攥着康熙铜钱看向镜子。最后，第八张脸睁开了眼。",
        manifest,
    )

    assert result.passed is True


def test_chapter_prose_promotion_blocks_forbidden_terms_and_body_causality() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        participants=["林渊", "王建业"],
        story_bible_context={"protagonist_forbidden_vocabulary": ["破案"]},
        project_metadata={
            "body_object_state_contract": {
                "tracked_objects": {"康熙铜钱": "必须说明位置"},
                "require_visible_cause_for_bleeding": True,
            },
            "ending_hook_contract": {
                "required_hook_target": "第八张脸",
                "forbidden_ending_modes": ["总结主题"],
            },
        },
    )

    result = validate_chapter_prose_for_promotion(
        "林渊开始破案。鼻血滴在康熙铜钱上。最后，第八张脸睁开了眼。",
        manifest,
    )

    assert result.passed is False
    assert any("破案" in item for item in result.violations)
    assert any("鼻血" in item for item in result.violations)


def test_chapter_prose_promotion_passes_clean_text() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        participants=["林渊", "王建业"],
        project_metadata={
            "body_object_state_contract": {
                "tracked_objects": {"康熙铜钱": "必须说明位置"},
                "require_visible_cause_for_bleeding": True,
            },
            "ending_hook_contract": {
                "required_hook_target": "第八张脸",
                "forbidden_ending_modes": ["总结主题"],
            },
        },
    )

    result = validate_chapter_prose_for_promotion(
        "林渊把康熙铜钱按在门槛上，镜面里第八张脸慢慢抬头。",
        manifest,
    )

    assert result.passed is True


def test_parse_prewrite_plan_accepts_fenced_json() -> None:
    plan = parse_prewrite_plan(
        """```json
{"characters_to_use":["林渊"],"time_anchors_to_use":["今夜"]}
```"""
    )

    assert plan.characters_to_use == ["林渊"]
    assert plan.time_anchors_to_use == ["今夜"]


def test_parse_prewrite_plan_coerces_deepseek_schema_drift() -> None:
    plan = parse_prewrite_plan(
        """{
          "characters_to_use": "林渊",
          "time_budget_plan": [],
          "body_object_state_plan": "铜钱在掌心",
          "ending_hook_type": ["新变量", "未答问题"],
          "ending_hook_target": ["病房玻璃"]
        }"""
    )

    assert plan.characters_to_use == ["林渊"]
    assert plan.time_budget_plan == {}
    assert plan.body_object_state_plan == {"summary": "铜钱在掌心"}
    assert plan.ending_hook_type == "新变量"
    assert plan.ending_hook_target == "病房玻璃"


def test_normalize_prewrite_plan_clamps_to_manifest_contract() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        scene_number=1,
        participants=["林渊", "苏婉宁"],
        scene_metadata={
            "time_budget_contract": {
                "start": "23:43",
                "deadline": "子时",
                "allowed_elapsed_events": ["验盒开封"],
                "forbid_untracked_travel": True,
            },
            "body_object_state_contract": {
                "tracked_objects": {"康熙铜钱": "必须说明位置"},
            },
        },
        project_metadata={
            "opening_causality_contract": {
                "protagonist_entry_motivation": "林渊必须验证檀木盒真假",
                "protagonist_function": "用林家验账法验物",
                "visible_failure_cost": "错信沈家会被旧卷牵走",
            },
            "ending_hook_contract": {
                "allowed_hook_types": ["新变量"],
                "required_hook_target": "母亲手札",
                "forbidden_ending_modes": ["总结主题"],
            },
        },
    )
    raw_plan = PrewritePlan(
        characters_to_use=["林渊", "沈家临时人"],
        time_budget_plan={"elapsed_events": ["骑车二十分钟去义庄"]},
        body_object_state_plan={},
        ending_hook_type="总结主题",
        ending_hook_target="下一章继续",
    )

    normalized = normalize_prewrite_plan_for_manifest(raw_plan, manifest)

    assert validate_prewrite_plan(normalized, manifest).passed is True
    assert normalized.characters_to_use == ["林渊"]
    assert normalized.time_budget_plan["elapsed_events"] == ["验盒开封"]
    assert normalized.body_object_state_plan["tracked_objects"] == {
        "康熙铜钱": "必须说明位置"
    }
    assert normalized.ending_hook_type == "新变量"
    assert normalized.ending_hook_target == "母亲手札"
    assert "总结主题" in normalized.ending_modes_to_avoid


def test_render_blocks_put_machine_contract_in_prompt() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        participants=["林渊"],
        scene_time_label="今夜",
    )
    plan = build_safe_prewrite_plan(manifest)

    contract_block = render_constraint_manifest_block(manifest)
    plan_block = render_prewrite_plan_block(plan)

    assert "写前约束清单" in contract_block
    assert "allowed_characters" in contract_block
    assert "已验证写作计划" in plan_block
    assert "characters_to_use" in plan_block


def test_compile_opening_contract_from_project_metadata() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        scene_number=1,
        participants=["林渊", "王建业"],
        scene_metadata={
            "time_budget_contract": {
                "start": "23:43",
                "deadline": "子时",
                "allowed_elapsed_events": ["上楼查镜", "接电话确认真王老板"],
                "forbid_untracked_travel": True,
            },
            "body_object_state_contract": {
                "tracked_objects": {"康熙铜钱": "必须连续说明在掌心/门板/镜框的位置"},
                "tracked_body_states": {"林渊出血": "必须先写伤口来源"},
            },
        },
        hook_requirement="第八张脸是谁",
        project_metadata={
            "opening_quality_contract": {
                "opening_incident": "王建业委托林渊十五分钟内看完十七栋。",
                "protagonist_edge": "林渊用阴阳眼、罗盘和青囊判断镜局。",
                "visible_loss_if_fail": "子时后第八张脸入镜。",
                "chapter_1_small_turn": "镜中第二张脸不是门外王老板。",
            },
            "ending_hook_contract": {
                "allowed_hook_types": ["身份反转", "未答问题"],
                "forbidden_ending_modes": ["总结主题", "作者式预告"],
            },
        },
    )

    assert manifest.opening_causality_contract is not None
    assert (
        manifest.opening_causality_contract.protagonist_entry_motivation
        == "王建业委托林渊十五分钟内看完十七栋。"
    )
    assert manifest.time_budget_contract is not None
    assert manifest.time_budget_contract.allowed_elapsed_events == [
        "上楼查镜",
        "接电话确认真王老板",
    ]
    assert manifest.body_object_state_contract is not None
    assert "康熙铜钱" in manifest.body_object_state_contract.tracked_objects
    assert manifest.ending_hook_contract is not None
    assert manifest.ending_hook_contract.allowed_hook_types == ["身份反转", "未答问题"]


def test_validate_prewrite_plan_rejects_missing_motivation_and_untracked_travel() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        scene_number=1,
        participants=["林渊", "王建业"],
        scene_metadata={
            "time_budget_contract": {
                "allowed_elapsed_events": ["上楼查镜"],
                "forbid_untracked_travel": True,
            }
        },
        project_metadata={
            "opening_causality_contract": {
                "protagonist_entry_motivation": "王建业委托林渊十五分钟内看完十七栋",
                "protagonist_function": "用阴阳眼、罗盘、青囊判断镜局规则",
                "visible_failure_cost": "子时后第八张脸入镜",
            },
            "ending_hook_contract": {
                "allowed_hook_types": ["身份反转"],
                "required_hook_target": "第八张脸",
                "forbidden_ending_modes": ["总结主题"],
            },
        },
    )
    plan = PrewritePlan(
        characters_to_use=["林渊", "王建业"],
        protagonist_function="用阴阳眼、罗盘、青囊判断镜局规则",
        visible_failure_cost="子时后第八张脸入镜",
        time_budget_plan={"elapsed_events": ["骑车二十分钟去旧事馆"]},
        ending_hook_type="总结主题",
        ending_hook_target="下一章继续查",
    )

    result = validate_prewrite_plan(plan, manifest)

    assert result.passed is False
    assert any("protagonist_entry_motivation" in item for item in result.violations)
    assert any("time_budget_plan" in item for item in result.violations)
    assert any("ending_hook_type" in item for item in result.violations)


def test_safe_opening_plan_satisfies_causality_contracts() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        scene_number=1,
        participants=["林渊"],
        scene_metadata={
            "time_budget_contract": {
                "start": "23:43",
                "deadline": "子时",
                "allowed_elapsed_events": ["上楼查镜"],
                "forbid_untracked_travel": True,
            },
            "body_object_state_contract": {
                "tracked_objects": {"康熙铜钱": "必须说明位置"},
            },
        },
        project_metadata={
            "opening_causality_contract": {
                "protagonist_entry_motivation": "王建业请林渊看宅",
                "protagonist_function": "林渊用阴阳眼和罗盘辨认镜局",
                "visible_failure_cost": "子时后入镜",
            }
        },
    )

    plan = build_safe_prewrite_plan(manifest)

    assert validate_prewrite_plan(plan, manifest).passed is True
    assert plan.protagonist_entry_motivation == "王建业请林渊看宅"
    assert plan.time_budget_plan["forbid_untracked_travel"] is True
    assert "康熙铜钱" in plan.body_object_state_plan["tracked_objects"]
    assert "总结主题" in plan.ending_modes_to_avoid


def test_prewrite_prompt_includes_methodology_for_early_chapters() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        scene_number=1,
        participants=["林渊"],
        scene_metadata={"location_name": "十七栋 23 层"},
    )
    prompt = render_prewrite_plan_prompt(
        manifest,
        language="zh-CN",
        pack=get_prompt_pack("suspense-mystery"),
        chapter_number=1,
    )

    assert "写作方法论参考" in prompt
    assert "信息密度" in prompt
    assert "弹簧法" in prompt
    assert "冲突筹码" in prompt


def test_prewrite_prompt_omits_density_rule_for_late_chapters() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=11,
        scene_number=1,
        participants=["林渊"],
        scene_metadata={"location_name": "十七栋 23 层"},
    )
    prompt = render_prewrite_plan_prompt(
        manifest,
        language="zh-CN",
        pack=get_prompt_pack("suspense-mystery"),
        chapter_number=11,
    )

    assert "写作方法论参考" in prompt
    assert "信息密度规则" not in prompt
    assert "弹簧法" in prompt


def test_empty_elapsed_whitelist_does_not_auto_reject_travel() -> None:
    """空白名单时 travel 检查不可满足，必须跳过而非恒拒绝（500章跑书 100% prewrite 失败根因）。"""
    manifest = compile_chapter_constraint_manifest(
        chapter_number=2,
        scene_number=1,
        participants=["陆沉"],
        scene_metadata={
            "time_budget_contract": {
                "forbid_untracked_travel": True,
            }
        },
        project_metadata={},
    )
    plan = PrewritePlan(
        characters_to_use=["陆沉"],
        time_budget_plan={"elapsed_events": ["骑车二十分钟去灵务局"]},
    )

    result = validate_prewrite_plan(plan, manifest)

    assert not any("allowed_elapsed_events" in item for item in result.violations)


def test_travel_violation_message_carries_whitelist() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        scene_number=1,
        participants=["陆沉"],
        scene_metadata={
            "time_budget_contract": {
                "allowed_elapsed_events": ["上楼查镜", "巡查记录归档"],
                "forbid_untracked_travel": True,
            }
        },
        project_metadata={},
    )
    plan = PrewritePlan(
        characters_to_use=["陆沉"],
        time_budget_plan={"elapsed_events": ["骑车二十分钟去旧事馆"]},
    )

    result = validate_prewrite_plan(plan, manifest)

    travel_violations = [
        item for item in result.violations if "allowed_elapsed_events" in item
    ]
    assert travel_violations
    assert "上楼查镜" in travel_violations[0]
    assert "巡查记录归档" in travel_violations[0]


def test_prewrite_prompt_surfaces_elapsed_event_whitelist() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        scene_number=1,
        participants=["陆沉"],
        scene_metadata={
            "time_budget_contract": {
                "allowed_elapsed_events": ["上楼查镜"],
                "forbid_untracked_travel": True,
            }
        },
        project_metadata={},
    )

    prompt = render_prewrite_plan_prompt(manifest, language="zh-CN")

    assert "时间预算硬规则" in prompt
    assert "上楼查镜" in prompt


def test_prewrite_prompt_warns_when_no_registered_elapsed_events() -> None:
    manifest = compile_chapter_constraint_manifest(
        chapter_number=1,
        scene_number=1,
        participants=["陆沉"],
        scene_metadata={
            "time_budget_contract": {
                "forbid_untracked_travel": True,
            }
        },
        project_metadata={},
    )

    prompt = render_prewrite_plan_prompt(manifest, language="zh-CN")

    assert "没有已登记的耗时事件" in prompt
