# ruff: noqa: RUF001

from __future__ import annotations

from bestseller.services.planning_readiness_gate import evaluate_planning_readiness


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
    assert "PLANNING_OBJECT_SIGNAL_UNBOUNDED" in report.blocking_issue_codes
    assert "PLANNING_KNOWLEDGE_BOUNDARY_LEAK" in report.blocking_issue_codes
