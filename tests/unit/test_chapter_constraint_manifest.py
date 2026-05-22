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
    parse_prewrite_plan,
    render_constraint_manifest_block,
    render_prewrite_plan_block,
    validate_prewrite_plan,
)

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


def test_parse_prewrite_plan_accepts_fenced_json() -> None:
    plan = parse_prewrite_plan(
        """```json
{"characters_to_use":["林渊"],"time_anchors_to_use":["今夜"]}
```"""
    )

    assert plan.characters_to_use == ["林渊"]
    assert plan.time_anchors_to_use == ["今夜"]


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
