# ruff: noqa: RUF001

from __future__ import annotations

from bestseller.services.planning_readiness_gate import (
    evaluate_chapter_outline_batch_planning_readiness,
    evaluate_planning_readiness,
)


def _complete_chapter() -> dict[str, object]:
    return {
        "chapter_number": 1,
        "opening_situation": "23:53，林渊收到十五分钟入账倒计时。",
        "main_conflict": "林渊必须在镜中名单落定前确认王建业是否认账。",
        "causal_contract": {
            "chapter_function": "黄金三章开钩",
            "opening_pressure": "倒计时十五分钟，电梯血线倒流。",
            "protagonist_flaw": "林渊习惯先算规则，压住恐惧不肯求助。",
            "protagonist_choice": "林渊选择开门核验王建业，而不是逃出楼道。",
            "visible_action_or_reaction": "林渊用康熙铜钱压住门缝。",
            "cost_or_tradeoff": "铜钱崩出缺口，父亲留下的唯一物件受损。",
            "gain_or_reveal": "穿衣镜里出现第七格空账。",
            "payoff": "读者看到镜债规则第一次落地。",
            "state_change": "林渊从旁观者变成账上的第七人。",
            "next_reader_desire": "读者想知道第七格为什么是林渊。",
            "tail_hook": "门外响起三短一长的敲门声。",
        },
        "methodology_contract": {
            "decision_protocol": {
                "viewpoint_character": "林渊",
                "decision_point": "十五分钟倒计时启动，王建业要求立刻开门。",
                "known_facts": ["开门可能触发替认", "铜钱只能短暂压住门缝"],
                "unknowns": ["门外敲门者身份", "镜中第七格为何空着"],
                "immediate_goal": "先确认王建业是否已经认账，再决定是否开门。",
                "options_considered": ["直接开门", "撤出楼道", "先用铜钱验门缝"],
                "obvious_safe_option": "撤出楼道并报警封楼。",
                "chosen_action": "先用铜钱验门缝，不让王建业继续说出认账词。",
                "why_not_safer_option": "撤离无法终止已启动的十五分钟替认，且会把风险留给楼内住户。",
                "personality_basis": "林渊谨慎、重证据，也不肯让无辜住户替自己承担风险。",
                "risk_control": "铜钱只压三息；异常扩大就斩断门绳退到楼梯间。",
                "expected_gain": "验证门缝血线是否代表替认已经开始。",
                "failure_cost": "铜钱损毁，林渊可能被镜面记名。",
                "new_information_or_pressure": "电梯血线倒流，倒计时只剩十五分钟。",
                "first_person_reasoning": "我先验最小的一步；证据不对就退，不能为了快把整栋楼押上。",
            }
        },
        "scenes": [
            {
                "scene_number": 1,
                "participants": ["林渊", "王建业"],
                "purpose": {
                    "story": "林渊用铜钱核验王建业的认账状态。",
                    "emotion": "恐惧被压进动作，读者感到倒计时逼近。",
                },
                "entry_state": {"reader": "十五分钟倒计时启动。"},
                "exit_state": {"reader": "林渊成了第七格。"},
                "target_word_count": 900,
                "methodology_contract": {
                    "conflict_stakes": "认错账会让林渊替王建业入账。",
                    "information_control_mode": "只展示镜债动作规则，不解释源头。",
                    "signature_image": "铜钱压住门缝时，血点从通字里冒出来。",
                    "cut_point": "三短一长的敲门声贴着门板响起。",
                },
            }
        ],
    }


def test_missing_scene_stakes_blocks_materialization() -> None:
    chapter = _complete_chapter()
    scene = chapter["scenes"][0]
    assert isinstance(scene, dict)
    scene["methodology_contract"] = {
        "information_control_mode": "只展示镜债动作规则，不解释源头。",
        "signature_image": "铜钱压住门缝时，血点从通字里冒出来。",
        "cut_point": "三短一长的敲门声贴着门板响起。",
    }

    report = evaluate_planning_readiness(chapter_outlines=[chapter])

    assert report.passed is False
    assert "PLANNING_SCENE_FIELD_MISSING" in report.blocking_issue_codes
    assert any("conflict_stakes" in key for key in report.missing_context_keys)


def test_missing_front_ten_tail_hook_blocks_outline() -> None:
    chapter = _complete_chapter()
    contract = chapter["causal_contract"]
    assert isinstance(contract, dict)
    contract.pop("tail_hook")
    contract.pop("next_reader_desire")
    chapter["hook_description"] = ""

    report = evaluate_planning_readiness(chapter_outlines=[chapter])

    assert report.passed is False
    assert "PLANNING_FRONT_TEN_FIELD_MISSING" in report.blocking_issue_codes
    assert "tail_hook" in report.repair_prompt


def test_complete_outline_passes() -> None:
    report = evaluate_planning_readiness(
        volume_plan={
            "goal": "林渊查清镜债第一环。",
            "obstacle": "三姓钱互相隐瞒。",
            "climax": "林渊在302门口完成第一次反认账。",
            "resolution": "王建业账页归档但父亲录音露出新债。",
            "reveal_budget": ["镜债动作规则", "第七格身份钩子"],
            "chapter_count_target": 10,
        },
        chapter_outlines=[_complete_chapter()],
        material_anchors=["青囊,康熙铜钱,镜债"],
    )

    assert report.passed is True
    assert report.blocking_findings == ()


def test_front_ten_outline_without_decision_context_is_advisory() -> None:
    # Decision-protocol completeness is surfaced as ADVISORY, never a hard block:
    # requiring the full first-person protocol on every front chapter killed
    # legitimate short-form paths (fanqie fallback segments) and reproduced the
    # "new gate kills good books" pattern. Decision intelligence is still enforced
    # at the LLM outline/chapter judge layer (decision_intelligence >= 0.84).
    chapter = _complete_chapter()
    chapter["methodology_contract"] = {}

    report = evaluate_planning_readiness(chapter_outlines=[chapter])

    assert report.passed is True
    assert "PROTAGONIST_DECISION_CONTEXT_INCOMPLETE" not in report.blocking_issue_codes
    audit_codes = {finding.code for finding in report.audit_findings}
    assert "PROTAGONIST_DECISION_CONTEXT_INCOMPLETE" in audit_codes
    assert any("known_facts" in key for key in report.missing_context_keys)


def test_planning_readiness_blocks_weak_phone_opening() -> None:
    chapter = _complete_chapter()
    chapter["opening_situation"] = "23:45，林渊接到王建业的求救电话。"
    chapter["causal_contract"]["opening_pressure"] = "23:45，林渊接到王建业的求救电话。"
    chapter["scenes"] = [
        {
            "scene_number": 1,
            "participants": ["林渊", "王建业"],
            "purpose": {"story": "林渊在旧铺接电话。", "emotion": "紧张"},
            "entry_state": {"reader": "旧铺清账。"},
            "exit_state": {"reader": "电话断线。"},
            "target_word_count": 900,
            "methodology_contract": {
                "conflict_stakes": "错过电话会死人。",
                "information_control_mode": "只给声音异常。",
                "signature_image": "账本边缘渗水。",
                "cut_point": "电话里传出第二个王建业的笑。",
            },
        }
    ]

    report = evaluate_planning_readiness(chapter_outlines=[chapter])

    assert report.passed is False
    assert "PLANNING_WEAK_MEDIATED_OPENING" in report.blocking_issue_codes


def test_planning_readiness_blocks_front_logic_hard_errors() -> None:
    chapter = _complete_chapter()
    chapter["opening_situation"] = "23:43，林渊赶到十七栋楼下。"
    chapter["causal_contract"]["object_signal_contract"] = ""
    chapter["scenes"] = [
        {
            "scene_number": 1,
            "participants": ["林渊", "张建军"],
            "purpose": {
                "story": "配送单显示寄件时间23:58。铜钱发烫。罗盘发烫。账页发烫。",
                "emotion": "荒诞感压住现实逻辑。",
            },
            "entry_state": {"reader": "林渊到现场。"},
            "exit_state": {"reader": "张建军敲门。"},
            "target_word_count": 900,
            "methodology_contract": {
                "conflict_stakes": "错判会让张建军被记名。",
                "information_control_mode": "先给物证。",
                "signature_image": "配送单上的23:58。",
                "cut_point": "张建军问：下一笔是不是我？",
            },
        }
    ]

    report = evaluate_planning_readiness(chapter_outlines=[chapter])

    assert report.passed is False
    assert "PLANNING_REAL_WORLD_PLAUSIBILITY_GAP" in report.blocking_issue_codes
    # Object-signal-unbounded is now detected genre-neutrally (any object + 发烫,
    # not a hardcoded 青囊 prop list).
    assert "PLANNING_OBJECT_SIGNAL_UNBOUNDED" in report.blocking_issue_codes
    # Knowledge-boundary leak detection was removed from this deterministic gate
    # (it hardcoded one detective book's cast/jargon, leaking it into every project)
    # and is now enforced genre-neutrally by the outline_commercial_judge.
    assert "PLANNING_KNOWLEDGE_BOUNDARY_LEAK" not in report.blocking_issue_codes


def test_evaluate_chapter_outline_batch_planning_readiness_accepts_dict_payload() -> None:
    batch = {
        "chapters": [
            _complete_chapter(),
            {
                "chapter_number": 2,
                "opening_situation": "17:59，林渊把张建军从门框里拉开来。",
                "main_conflict": "张建军是否真的能认账成了本章的第一关。",
                "causal_contract": {
                    "chapter_function": "把否认者逼进账本可见区。",
                    "opening_pressure": "17:59，林渊必须立刻决定是追责还是先稳住对方。",
                    "protagonist_flaw": "林渊仍然先猜规则再做判断。",
                    "protagonist_choice": "林渊先发起一次可见核验。",
                    "visible_action": "林渊抓起门把手并拉开门口。",
                    "cost": "迟疑会让关键账据散失。",
                    "gain_or_reveal": "对方当场露出错认账迹象。",
                    "state_change": "林渊从猜测转入实拍核验。",
                    "next_reader_desire": "读者想知道他会如何收束第三方。",
                    "pacing_mode": "窄口推进",
                    "emotion_phase": "戒备上升",
                    "loop_position": "开局压迫",
                    "hooks_to_resolve": ["对方是否会立刻逃跑？"],
                    "hooks_to_plant": ["下一章会出现更高处置压力。"],
                    "relationship_debts": ["他与张建军的信任仍在倒退。"],
                    "conflict_buffs": ["时间窗口快速缩短。"],
                    "conflict_stakes": "核验不成功会失去窗口。",
                    "required_payoff": "拿到第一笔可靠线索。",
                    "payoff": "拿到第一笔可靠线索。",
                },
                "methodology_contract": _complete_chapter()["methodology_contract"],
                "scenes": [
                    {
                        "scene_number": 1,
                        "participants": ["林渊", "张建军"],
                        "purpose": {
                            "story": "林渊让张建军复述入账过程。",
                            "emotion": "紧张中带着逼近。",
                        },
                        "entry_state": {"reader": "门口很晚的风压住脚步声。"},
                        "exit_state": {"reader": "门口出现新的可见动作。"},
                        "target_word_count": 800,
                        "methodology_contract": {
                            "conflict_stakes": "若不及时核验，张建军会改口。",
                            "conflict_buffs": ["门外有监控，错一步会被抓到。"],
                            "hook_type": "choice_pressure",
                            "spotlight_character": "林渊",
                            "information_control_mode": "先给读者看到张建军眼神再压缩解释。",
                            "camera_distance": "中近景，镜头卡在门边。",
                            "reveal_mode": "由张建军口误引出账据矛盾。",
                            "signature_image": "张建军手上的账簿边缘起了白雾。",
                            "cut_point": "张建军的喉结一抖，准备逃离。",
                        },
                    }
                ],
            },
        ]
    }

    report = evaluate_chapter_outline_batch_planning_readiness(batch)
    assert report.passed is True
    assert report.blocking_issue_codes == ()
