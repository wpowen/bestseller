from __future__ import annotations

from bestseller.domain.chapter_generation_input import ChapterGenerationInputBundle
from bestseller.services.chapter_predraft_quality_gate import (
    evaluate_chapter_predraft_quality,
)
from bestseller.services.methodology_application_gate import (
    build_methodology_application_contract,
)


def _relationship_debt() -> dict[str, object]:
    return {
        "debtor": "林渊",
        "creditor": "王建业",
        "evidence_or_handle": "缺角铜钱证明林渊没救回王建业",
        "due_condition": "张建军带同款旧镜钥匙上门时",
        "breach_consequence": "第一场失败无法回响，主角代价失真",
        "repayment_modes": ["追出王建业最后动作", "保留缺角铜钱作为失败凭据"],
    }


def _decision_protocol() -> dict[str, object]:
    return {
        "viewpoint_character": "林渊",
        "decision_point": "倒计时内镜面即将完成替认。",
        "known_facts": ["压门缝能短暂阻断替认", "铜钱只能压住三息"],
        "unknowns": ["镜外敲门者是谁"],
        "immediate_goal": "阻止王建业被替认并留下物证。",
        "options_considered": ["直接报警", "等待天亮", "压住门缝"],
        "obvious_safe_option": "撤离并报警封楼。",
        "chosen_action": "林渊在倒计时内压住门缝。",
        "why_not_safer_option": "撤离无法终止已经启动的替认，等待会错过救援窗口。",
        "personality_basis": "林渊谨慎、重证据，但不会放弃眼前能救的人。",
        "risk_control": "只压三息，留下退路；镜面记名就立刻断开铜钱。",
        "expected_gain": "救下王建业并取得镜面血点物证。",
        "failure_cost": "王建业被收账，林渊被镜面记名。",
        "new_information_or_pressure": "替认倒计时只剩三息。",
        "first_person_reasoning": "我只压三息，救不下来就断开，不能站着等他被收走。",
    }


def test_chapter_predraft_quality_gate_passes_rich_front_chapter_input() -> None:
    scenes = (
        {
            "scene_number": 1,
            "gate_function": "opening_pull: first-page pressure",
            "visible_progress": "林渊在倒计时内压住门缝。",
            "reader_payoff": "铜钱血点暴露王建业撒谎。",
            "ending_hook_payload": "门外响起三短一长。",
            "methodology_contract": {
                "stakes": "开门即替认。",
                "pressure_stack": "倒计时、铜钱发烫、门外敲门。",
                "focus_character": "林渊",
                "reveal_mode": "先动作后解释。",
                "signature_image": "铜钱血点",
                "breakpoint": "三短一长敲门。",
                "relationship_debts": [_relationship_debt()],
            },
        },
    )
    bundle = ChapterGenerationInputBundle(
        chapter={"chapter_number": 1},
        scenes=scenes,
        acceptance_contract={
            "chapter_number": 1,
            "must_deliver": [
                {"label": "chapter_goal", "value": "确认王建业认账"},
                {"label": "main_conflict", "value": "是否开门替认"},
                {"label": "information_release", "value": "认动作不认因果"},
                {"label": "closing_hook", "value": "铜钱通字冒血"},
            ],
            "scene_gate_targets": [
                {
                    "scene_number": 1,
                    "gate_function": "opening_pull",
                    "visible_progress": "压住门缝",
                    "reader_payoff": "识破",
                    "ending_hook_payload": "敲门声",
                }
            ],
            "front_position_rules": {
                "opening_must_start_with_pressure": True,
                "ending_hook_must_add_new_information": True,
                "ending_must_land_on_completed_scene_frame": True,
                "real_world_evidence_must_be_plausible_or_marked_impossible": True,
                "object_signal_meaning_must_be_stable": True,
            },
            "knowledge_boundary_contract": {
                "specialist_rule_terms": ["认账", "入账", "替认"],
                "allowed_explainers": ["林渊"],
            },
            "object_signal_contract": {
                "rule": "铜钱裂口代表代价，血点代表债务接触。",
            },
            "methodology_application_contract": build_methodology_application_contract(
                chapter_number=1,
                chapter_title="十五分钟入账",
                chapter_contract={
                    "visible_action_or_reaction": "林渊在倒计时内压住门缝。",
                    "conflict_stakes": "开门即替认。",
                    "loop_position": "危机-判断-代价-新钩子",
                    "relationship_debts": [_relationship_debt()],
                    "decision_protocol": _decision_protocol(),
                },
                scene_cards=scenes,
            ),
        },
        required_context_keys=("chapter.goal",),
        missing_context_keys=(),
    )

    report = evaluate_chapter_predraft_quality(bundle)

    assert report.passed is True
    assert report.blocked is False


def test_chapter_predraft_quality_gate_blocks_thin_front_chapter_input() -> None:
    bundle = ChapterGenerationInputBundle(
        chapter={"chapter_number": 1},
        scenes=(
            {
                "scene_number": 1,
                "gate_function": "continuity: bridge",
                "visible_progress": "",
                "reader_payoff": "",
                "ending_hook_payload": "",
                "methodology_contract": {},
            },
        ),
        acceptance_contract={
            "chapter_number": 1,
            "must_deliver": [
                {"label": "chapter_goal", "value": "确认王建业认账"},
            ],
            "scene_gate_targets": [],
            "front_position_rules": {},
        },
        required_context_keys=("chapter.goal", "chapter_acceptance_contract"),
        missing_context_keys=("chapter_acceptance_contract",),
    )

    report = evaluate_chapter_predraft_quality(bundle)
    codes = {finding.code for finding in report.blocking_issues}

    assert report.passed is False
    assert report.blocked is True
    assert "PREDRAFT_CONTEXT_MISSING" in codes
    assert "PREDRAFT_SCENE_GATE_TARGETS_EMPTY" in codes
    assert "PREDRAFT_GOLDEN_THREE_OPENING_WEAK" in codes
    assert "PREDRAFT_KNOWLEDGE_BOUNDARY_CONTRACT_MISSING" in codes
