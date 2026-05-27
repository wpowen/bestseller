from __future__ import annotations

from bestseller.services.methodology_application_gate import (
    build_methodology_application_contract,
    evaluate_methodology_application,
)


def _scene(
    *,
    hook: str,
    emotion: str,
    image: str,
) -> dict[str, object]:
    return {
        "hook_requirement": hook,
        "purpose": {"story": f"推进：{hook}", "emotion": emotion},
        "metadata_json": {
            "methodology_contract": {
                "signature_image": image,
                "conflict_stakes": "误判会让账线闭合。",
                "pressure_stack": ["倒计时", "证据污染"],
                "focus_character": "林渊",
                "reveal_mode": "先物证后解释。",
                "breakpoint": hook,
            }
        },
    }


def test_methodology_application_gate_blocks_missing_front_ten_contract() -> None:
    report = evaluate_methodology_application(
        chapter_number=1,
        chapter_metadata={},
        scene_cards=[
            _scene(
                hook="门外响起三短一长。",
                emotion="林渊怕再次救晚。",
                image="铜钱缺角渗黑水。",
            )
        ],
    )

    assert report.passed is False
    assert "METHODOLOGY_APPLICATION_CONTRACT_MISSING" in {
        issue.code for issue in report.blocking_issues
    }


def test_methodology_application_gate_blocks_repeated_scene_hooks_and_templates() -> None:
    scenes = [
        _scene(
            hook="手机黑屏里多出一张贴在屏幕外侧的脸。",
            emotion="林渊从被动承压转为做出一个带代价的判断，读者能看见害怕、犹豫或信任变化。",
            image="屏幕外侧的脸鼻尖压扁。",
        ),
        _scene(
            hook="手机黑屏里多出一张贴在屏幕外侧的脸。",
            emotion="林渊从被动承压转为做出一个带代价的判断，读者能看见害怕、犹豫或信任变化。",
            image="屏幕外侧的脸鼻尖压扁。",
        ),
    ]
    report = evaluate_methodology_application(
        chapter_number=9,
        chapter_metadata={
            "methodology_application_contract": build_methodology_application_contract(
                chapter_number=9,
                chapter_title="屏幕外的脸",
                chapter_contract={"visible_action_or_reaction": "拆开不可能快递。"},
                scene_cards=scenes,
            )
        },
        scene_cards=scenes,
    )

    codes = {issue.code for issue in report.blocking_issues}
    assert "METHODOLOGY_SCENE_HOOK_REPEATED" in codes
    assert "METHODOLOGY_SIGNATURE_IMAGE_REPEATED" in codes
    assert "METHODOLOGY_SCENE_EMOTION_TEMPLATE_REPEATED" in codes


def test_methodology_application_contract_covers_front_ten_required_cards() -> None:
    contract = build_methodology_application_contract(
        chapter_number=3,
        chapter_title="半账不能替",
        chapter_contract={"visible_action_or_reaction": "林渊用缓存视频验证半账。"},
        scene_cards=[],
    )

    card_ids = {item["card_id"] for item in contract["applications"]}
    assert "plova.opening.anti_pitfall" in card_ids
    assert "plova.mainline.stage_goal_obstacle_result" in card_ids
    assert "platform.character_debt_ledger" in card_ids


def test_methodology_application_gate_blocks_placeholder_relationship_debt() -> None:
    scenes = [
        {
            "hook_requirement": "门外响起三短一长。",
            "purpose": {"emotion": "林渊怕再次救晚。"},
            "metadata_json": {
                "methodology_contract": {
                    "signature_image": "铜钱缺角渗黑水。",
                    "relationship_debts": ["本场必须改变至少一组人物的信任、亏欠或隐瞒状态"],
                }
            },
        }
    ]
    report = evaluate_methodology_application(
        chapter_number=3,
        chapter_metadata={
            "methodology_application_contract": build_methodology_application_contract(
                chapter_number=3,
                chapter_title="半账不能替",
                chapter_contract={"visible_action_or_reaction": "林渊主动查半账。"},
                scene_cards=scenes,
            )
        },
        scene_cards=scenes,
    )

    assert "METHODOLOGY_RELATIONSHIP_DEBT_PLACEHOLDER" in {
        issue.code for issue in report.blocking_issues
    }
