from __future__ import annotations

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services.methodology_overlay import (
    normalize_chapter_overlay,
    normalize_scene_overlay,
    render_overlay_prompt_block,
    resolve_methodology_contract_mode,
    validate_chapter_methodology_contract,
    validate_scene_methodology_contract,
)

pytestmark = pytest.mark.unit


def test_normalize_chapter_overlay_accepts_pressure_stack_aliases() -> None:
    overlay = normalize_chapter_overlay(
        {
            "stakes": "林渊若失败会失去师父最后的线索。",
            "pressure_stack": {
                "deadline": "子时前镜门关闭。",
                "exposure_risk": "阴阳眼会被周老板看穿。",
            },
            "plant_hooks": ["镜门后的人影是谁？"],
            "relationship_promises": ["答应妹妹天亮前带药回家。"],
        }
    )

    assert overlay["conflict_stakes"].startswith("林渊若失败")
    assert overlay["conflict_buffs"] == [
        "时限: 子时前镜门关闭。",
        "暴露风险: 阴阳眼会被周老板看穿。",
    ]
    assert overlay["hooks_to_plant"] == ["镜门后的人影是谁？"]
    assert overlay["relationship_debts"] == ["答应妹妹天亮前带药回家。"]


def test_normalize_scene_overlay_keeps_camera_and_cut_point() -> None:
    overlay = normalize_scene_overlay(
        {
            "stakes": "交出铜钱就会断掉父亲线索。",
            "camera": "特写",
            "reveal_mode": "读者先知，角色误判",
            "signature_image": "铜钱在雨水里渗出黑血。",
            "ending_cut": "周老板说出父亲旧名的前一秒。",
        }
    )

    assert overlay["camera_distance"] == "特写"
    assert overlay["reveal_mode"] == "读者先知，角色误判"
    assert overlay["signature_image"] == "铜钱在雨水里渗出黑血。"
    assert overlay["cut_point"] == "周老板说出父亲旧名的前一秒。"


def test_render_overlay_prompt_block_includes_scene_execution_terms() -> None:
    block = render_overlay_prompt_block(
        scene_overlay={
            "conflict_stakes": "失败会暴露阴阳眼。",
            "conflict_buffs": ["时限: 子时前", "社会压力: 众人围观"],
            "camera_distance": "近景到特写",
            "signature_image": "铜镜裂纹映出第二张脸。",
            "relationship_debts": ["不能让师父知道自己动用了禁术。"],
        },
        language="zh-CN",
    )

    assert "方法论执行覆盖层" in block
    assert "失败会暴露阴阳眼" in block
    assert "近景到特写" in block
    assert "铜镜裂纹映出第二张脸" in block
    assert "不能让师父知道自己动用了禁术" in block


def test_normalize_scene_overlay_accepts_action_scene_fields() -> None:
    overlay = normalize_scene_overlay(
        {
            "fight_objective": "夺回铜镜。",
            "failure_cost": "失败会暴露阴阳眼。",
            "enemy_advantage": "周老板占据祠堂机关。",
            "strategy_shift": "从正面硬闯改为诱敌离开香案。",
            "fight_emotion": "林渊不想再让妹妹替自己受罚。",
            "fight_turn": "铜镜裂纹映出第二个周老板。",
            "after_state_change": "林渊拿到线索但被全镇通缉。",
            "aftereffect": "下一场必须先藏身药铺。",
        }
    )

    assert overlay["fight_objective"] == "夺回铜镜。"
    assert overlay["failure_cost"] == "失败会暴露阴阳眼。"
    assert overlay["opponent_advantage"] == "周老板占据祠堂机关。"
    assert overlay["tactic_shift"] == "从正面硬闯改为诱敌离开香案。"
    assert overlay["emotion_driver"] == "林渊不想再让妹妹替自己受罚。"
    assert overlay["turning_point"] == "铜镜裂纹映出第二个周老板。"
    assert overlay["exit_state_delta"] == "林渊拿到线索但被全镇通缉。"
    assert overlay["next_aftereffect"] == "下一场必须先藏身药铺。"


def test_action_scene_methodology_validator_requires_action_contract_fields() -> None:
    findings = validate_scene_methodology_contract(
        {
            "stakes": "失败会暴露阴阳眼。",
            "pressure_stack": {"deadline": "香灭前必须离开祠堂。"},
            "hook_type": "threat",
            "spotlight_character": "林渊",
            "information_control_mode": "reader_knows_less",
            "camera": "近景",
            "reveal_mode": "同步发现",
            "signature_image": "铜镜裂纹映出第二张脸。",
            "ending_cut": "周老板拔出铜镜里的影子。",
            "action_sequence": ["林渊格挡铜镜黑线。"],
        },
        chapter_number=2,
        scene_number=1,
        scene_type="fight",
        participant_count=1,
    )

    codes = {finding.code for finding in findings}

    assert "SCENE_METHODOLOGY_ACTION_FIELD_MISSING" in codes


def test_outline_schema_accepts_scene_methodology_alias() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "methodology-scene-alias",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "铜镜",
                    "goal": "林渊接下铜镜委托。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_methodology_contract": {
                                "stakes": "拒绝委托就断掉父亲线索。",
                                "camera": "近景",
                                "ending_cut": "铜镜映出父亲旧名。",
                            },
                        }
                    ],
                }
            ],
        }
    )

    overlay = batch.chapters[0].scenes[0].methodology_contract

    assert overlay["stakes"] == "拒绝委托就断掉父亲线索。"
    assert overlay["camera"] == "近景"


def test_resolve_methodology_contract_mode_prefers_project_metadata() -> None:
    project = type(
        "ProjectStub",
        (),
        {"metadata_json": {"methodology_contract_mode": "strict"}},
    )()
    settings = type(
        "SettingsStub",
        (),
        {"pipeline": type("PipelineStub", (), {"methodology_contract_mode": "warn"})()},
    )()

    assert resolve_methodology_contract_mode(project, settings=settings) == "strict"


def test_chapter_methodology_validator_catches_scope_mismatch() -> None:
    findings = validate_chapter_methodology_contract(
        {
            "stakes": "林渊失败会失去父亲旧名线索。",
            "pressure_stack": {"deadline": "子时前镜门关闭。"},
            "pacing_mode": "accelerate",
            "emotion_phase": "compress",
            "loop_position": "action",
            "hooks_to_plant": ["父亲为何签过镜债？"],
            "camera_distance": "特写",
        },
        chapter_number=3,
    )
    codes = {finding.code for finding in findings}

    assert "CHAPTER_METHODOLOGY_SCOPE_MISMATCH" in codes


def test_scene_methodology_validator_requires_scene_execution_fields() -> None:
    findings = validate_scene_methodology_contract(
        {
            "stakes": "拒绝委托就断掉父亲线索。",
            "pressure_stack": {"exposure_risk": "阴阳眼会被周老板看穿。"},
            "camera": "近景",
            "signature_image": "铜镜裂纹映出第二张脸。",
            "pacing_mode": "accelerate",
        },
        chapter_number=1,
        scene_number=2,
        scene_type="confrontation",
        participant_count=2,
    )
    codes = {finding.code for finding in findings}

    assert "SCENE_METHODOLOGY_FIELD_MISSING" in codes
    assert "SCENE_METHODOLOGY_ACTION_SEQUENCE_MISSING" in codes
    assert "SCENE_METHODOLOGY_RELATIONSHIP_DEBT_MISSING" in codes
    assert "SCENE_METHODOLOGY_SCOPE_MISMATCH" in codes
