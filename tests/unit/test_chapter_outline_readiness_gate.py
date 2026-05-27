# ruff: noqa: RUF001

from __future__ import annotations

from bestseller.services.chapter_outline_readiness_gate import (
    evaluate_chapter_outline_readiness,
)
from bestseller.services.methodology_application_gate import (
    build_methodology_application_contract,
)


def _methodology_contract() -> dict[str, object]:
    return {
        "stakes": "林渊必须在老宅井口封死前确认账纸上的下一名债主。",
        "pressure_stack": ["八点前赶到十七栋", "青囊秘卷半卷不动"],
        "focus_character": "林渊",
        "reveal_mode": "以账纸水痕和镜影错位给出可见证据。",
        "signature_image": "青囊秘卷停在空白半页，墨线渗成井口。",
        "breakpoint": "井口先封，钱婆婆只肯说半句。",
        "relationship_debts": [_relationship_debt()],
    }


def _relationship_debt() -> dict[str, object]:
    return {
        "debtor": "林渊",
        "creditor": "小雨",
        "evidence_or_handle": "半账线在现场加深，林渊必须解释错判",
        "due_condition": "下一场小雨追问半账是否会长满时",
        "breach_consequence": "小雨不再信任林渊的救援判断",
        "repayment_modes": ["主动解释", "用新证据补回错判"],
    }


def _decision_protocol() -> dict[str, object]:
    return {
        "chosen_action": "林渊用现场证据判断镜债边界。",
        "alternatives_rejected": ["直接报警", "等待天亮", "继续追问受害者"],
        "why_this_not_that": "倒计时正在收紧，只有现场证据能立刻改变下一步行动。",
        "constraint": "铜钱只能短暂压住边界，不能替主角解释规则。",
        "wrong_choice_loss": "无辜者被拖入账线，林渊失去救援窗口。",
    }


def _methodology_application_contract(
    chapter_number: int,
    scenes: list[dict[str, object]],
) -> dict[str, object]:
    return build_methodology_application_contract(
        chapter_number=chapter_number,
        chapter_title="子时前，镜中缺一张脸",
        chapter_contract={
            "visible_action_or_reaction": "林渊用现场证据判断镜债边界。",
            "conflict_stakes": "误判会让无辜者被拖入账线。",
            "loop_position": "危机-判断-代价-新钩子",
            "relationship_debts": [_relationship_debt()],
            "decision_protocol": _decision_protocol(),
        },
        scene_cards=scenes,
    )


def test_blocks_collapsed_scene_budget_and_stale_auto_repair_residue() -> None:
    report = evaluate_chapter_outline_readiness(
        chapter_number=85,
        chapter_title="十七栋半卷不动",
        chapter_target_word_count=2200,
        chapter_metadata={},
        scene_cards=[
            {
                "scene_number": 1,
                "target_word_count": 364,
                "purpose": {"story": "林渊赶往旧城。"},
                "metadata_json": {
                    "auto_repair_adjusted_target_word_count": 364,
                    "methodology_contract": _methodology_contract(),
                },
            },
            {
                "scene_number": 2,
                "target_word_count": 364,
                "purpose": {"story": "周德旺账纸露出水痕。"},
                "metadata_json": {"methodology_contract": _methodology_contract()},
            },
            {
                "scene_number": 3,
                "target_word_count": 364,
                "purpose": {"story": "半卷青囊不动。"},
                "metadata_json": {"methodology_contract": _methodology_contract()},
            },
        ],
    )

    codes = {issue.code for issue in report.issues}
    assert report.verdict == "blocked"
    assert "OUTLINE_SCENE_BUDGET_TOO_LOW" in codes
    assert "OUTLINE_SCENE_TARGET_TOO_LOW" in codes
    assert "OUTLINE_STALE_AUTO_REPAIR_RESIDUE" in codes


def test_repaired_chapter_85_scene_cards_pass_readiness_gate() -> None:
    report = evaluate_chapter_outline_readiness(
        chapter_number=85,
        chapter_title="十七栋半卷不动",
        chapter_target_word_count=2200,
        chapter_metadata={},
        scene_cards=[
            {
                "scene_number": 1,
                "target_word_count": 720,
                "purpose": {"story": "林渊和苏婉宁在旧城三点确认十七栋门牌与井口声响。"},
                "entry_state": {"reader": "八点前必须赶到十七栋。"},
                "exit_state": {"reader": "门牌背后露出周德旺账纸。"},
                "metadata_json": {"methodology_contract": _methodology_contract()},
            },
            {
                "scene_number": 2,
                "target_word_count": 760,
                "purpose": {"story": "钱婆婆逼林渊用周德旺账纸证明林家没有把井契私吞。"},
                "entry_state": {"reader": "账纸来自门牌背面。"},
                "exit_state": {"reader": "镜影提示半卷秘卷不是缺页而是锁页。"},
                "metadata_json": {"methodology_contract": _methodology_contract()},
            },
            {
                "scene_number": 3,
                "target_word_count": 740,
                "purpose": {"story": "林渊让青囊秘卷停在空白半页，确认老宅井口先封。"},
                "entry_state": {"reader": "锁页必须在八点前破开。"},
                "exit_state": {"reader": "井口先封形成下一章倒计时。"},
                "metadata_json": {"methodology_contract": _methodology_contract()},
            },
        ],
    )

    assert report.passed is True
    assert report.issues == ()


def test_allows_commercial_dynamic_scene_budget_under_3500_words() -> None:
    scenes = [
        {
            "scene_number": 1,
            "target_word_count": 824,
            "purpose": {"story": "林渊发现门禁记录异常。", "emotion": "林渊怕再次失手。"},
            "entry_state": {"reader": "林渊赶到十七栋楼下。"},
            "exit_state": {"reader": "门禁记录指向303。"},
            "hook_requirement": "303门缝里有血往上爬。",
            "metadata_json": {"methodology_contract": _methodology_contract()},
        },
        {
            "scene_number": 2,
            "target_word_count": 824,
            "purpose": {"story": "铜钱发烫，镜中少了一张脸。", "emotion": "王建业不敢承认进过303。"},
            "entry_state": {"reader": "林渊进入电梯。"},
            "exit_state": {"reader": "镜中少了一张脸。"},
            "hook_requirement": "穿衣镜里空出一个人形水印。",
            "metadata_json": {
                "methodology_contract": {
                    **_methodology_contract(),
                    "signature_image": "穿衣镜里空出一个人形水印。",
                }
            },
        },
        {
            "scene_number": 3,
            "target_word_count": 824,
            "purpose": {"story": "父亲录音留下第一条禁忌。", "emotion": "林渊被父亲旧训刺痛。"},
            "entry_state": {"reader": "王建业躲在沙发后。"},
            "exit_state": {"reader": "禁忌被现场动作触发。"},
            "hook_requirement": "录音里只剩三短一长的敲击。",
            "metadata_json": {
                "methodology_contract": {
                    **_methodology_contract(),
                    "signature_image": "录音笔红点在黑暗里闪。",
                }
            },
        },
        {
            "scene_number": 4,
            "target_word_count": 824,
            "purpose": {"story": "王建业惨叫，第一笔账落下。", "emotion": "林渊必须承认自己救晚了。"},
            "entry_state": {"reader": "子时将到。"},
            "exit_state": {"reader": "王建业生死不明。"},
            "hook_requirement": "门外响起三短一长。",
            "metadata_json": {
                "methodology_contract": {
                    **_methodology_contract(),
                    "signature_image": "门外猫眼黑成一枚铜钱孔。",
                }
            },
        },
    ]
    report = evaluate_chapter_outline_readiness(
        chapter_number=1,
        chapter_title="子时前，镜中缺一张脸",
        chapter_target_word_count=2200,
        chapter_metadata={
            "methodology_application_contract": _methodology_application_contract(1, scenes)
        },
        scene_cards=scenes,
    )

    assert report.passed is True
    assert report.metrics["scene_target_word_count_sum"] == 3296
    assert report.metrics["scene_sum_max_threshold"] == 3500


def test_blocks_front_chapter_phone_opening_without_visible_pressure() -> None:
    report = evaluate_chapter_outline_readiness(
        chapter_number=1,
        chapter_title="子时前，镜中缺一张脸",
        chapter_target_word_count=2200,
        chapter_metadata={"opening_situation": "23:45，林渊接到王建业的求救电话。"},
        scene_cards=[
            {
                "scene_number": 1,
                "target_word_count": 550,
                "purpose": {"story": "林渊在旧铺接电话，听到倒计时。"},
                "entry_state": {"reader": "旧铺清账。"},
                "exit_state": {"reader": "电话断线。"},
                "hook_requirement": "电话里出现第二个王建业的笑声。",
                "metadata_json": {"methodology_contract": _methodology_contract()},
            }
        ],
    )

    assert report.verdict == "blocked"
    assert "OUTLINE_WEAK_MEDIATED_OPENING" in {issue.code for issue in report.issues}


def test_blocks_front_chapter_late_night_delivery_hard_error() -> None:
    report = evaluate_chapter_outline_readiness(
        chapter_number=1,
        chapter_title="子时前，镜中缺一张脸",
        chapter_target_word_count=2200,
        chapter_metadata={},
        scene_cards=[
            {
                "scene_number": 1,
                "target_word_count": 550,
                "purpose": {"story": "张建军拿到配送单，寄件时间写着23:58。"},
                "entry_state": {"reader": "张建军上门。"},
                "exit_state": {"reader": "配送单确认王建业寄出旧镜。"},
                "hook_requirement": "配送单上写着23:58。",
                "metadata_json": {"methodology_contract": _methodology_contract()},
            }
        ],
    )

    assert report.verdict == "blocked"
    assert "OUTLINE_REAL_WORLD_PLAUSIBILITY_GAP" in {
        issue.code for issue in report.issues
    }


def test_blocks_outline_that_uses_its_own_forbidden_signal_contract() -> None:
    report = evaluate_chapter_outline_readiness(
        chapter_number=2,
        chapter_title="第一名否认者",
        chapter_target_word_count=2200,
        chapter_metadata={
            "object_signal_contract": {
                "forbidden_signals": ["铜钱发烫", "电话带人入场"]
            }
        },
        scene_cards=[
            {
                "scene_number": 1,
                "target_word_count": 550,
                "purpose": {"story": "林渊到303门口，铜钱发烫提醒他门里有东西。"},
                "entry_state": {"reader": "林渊在303门口。"},
                "exit_state": {"reader": "张建军被留下问话。"},
                "hook_requirement": "湿纸条背面洇出反字。",
                "metadata_json": {"methodology_contract": _methodology_contract()},
            }
        ],
    )

    assert report.verdict == "blocked"
    assert "OUTLINE_FORBIDDEN_SIGNAL_CONFLICT" in {
        issue.code for issue in report.issues
    }


def test_rejected_alternatives_do_not_self_hit_forbidden_signal_contract() -> None:
    report = evaluate_chapter_outline_readiness(
        chapter_number=10,
        chapter_title="陈默的半账",
        chapter_target_word_count=2200,
        chapter_metadata={
            "object_signal_contract": {"forbidden_signals": ["铜钱救场"]},
            "methodology_contract": {
                **_methodology_contract(),
                "decision_protocol": {
                    **_decision_protocol(),
                    "alternatives_rejected": ["继续拖延", "用铜钱救场"],
                    "chosen_action": "林渊让陈默交出寄件联，只承担证据半账。",
                },
            },
        },
        scene_cards=[
            {
                "scene_number": 1,
                "target_word_count": 2200,
                "purpose": {"story": "陈默交出寄件联，只承担证据半账。"},
                "entry_state": {"reader": "视频自播。"},
                "exit_state": {"reader": "对称半账线出现。"},
                "hook_requirement": "门缝回执写着306已结清又被划掉。",
                "metadata_json": {"methodology_contract": _methodology_contract()},
            }
        ],
    )

    assert "OUTLINE_FORBIDDEN_SIGNAL_CONFLICT" not in {
        issue.code for issue in report.issues
    }


def test_blocks_scene_contract_that_contains_forbidden_action_surface() -> None:
    report = evaluate_chapter_outline_readiness(
        chapter_number=2,
        chapter_title="第一名否认者",
        chapter_target_word_count=2200,
        chapter_metadata={},
        scene_cards=[
            {
                "scene_number": 1,
                "target_word_count": 550,
                "purpose": {"story": "林渊用湿纸条挡住门缝镜光。"},
                "entry_state": {"reader": "小雨被门缝镜光扫到。"},
                "exit_state": {"reader": "黑线停在半圈。"},
                "hook_requirement": "湿纸条压在小雨手腕影子上。",
                "forbidden_actions": [
                    "不得把湿纸条按在、贴在或压在小雨手腕上；湿纸条只能挡门缝镜光。"
                ],
                "metadata_json": {"methodology_contract": _methodology_contract()},
            }
        ],
    )

    assert report.verdict == "blocked"
    assert "OUTLINE_SCENE_FORBIDDEN_ACTION_CONFLICT" in {
        issue.code for issue in report.issues
    }


def test_blocks_non_expert_rule_knowledge_in_front_hook() -> None:
    report = evaluate_chapter_outline_readiness(
        chapter_number=1,
        chapter_title="子时前，镜中缺一张脸",
        chapter_target_word_count=2200,
        chapter_metadata={},
        scene_cards=[
            {
                "scene_number": 1,
                "target_word_count": 550,
                "purpose": {"story": "林渊赶到十七栋现场。"},
                "entry_state": {"reader": "林渊到楼下。"},
                "exit_state": {"reader": "张建军敲门。"},
                "hook_requirement": "张建军敲门问：下一笔是不是我？",
                "metadata_json": {"methodology_contract": _methodology_contract()},
            }
        ],
    )

    assert report.verdict == "blocked"
    assert "OUTLINE_KNOWLEDGE_BOUNDARY_LEAK" in {
        issue.code for issue in report.issues
    }


def test_blocks_unresolved_chapter_rewrite_tasks() -> None:
    report = evaluate_chapter_outline_readiness(
        chapter_number=85,
        chapter_title="十七栋半卷不动",
        chapter_target_word_count=2200,
        chapter_metadata={},
        scene_cards=[
            {
                "scene_number": 1,
                "target_word_count": 2200,
                "metadata_json": {"methodology_contract": _methodology_contract()},
            }
        ],
        pending_rewrite_task_count=1,
    )

    assert report.verdict == "blocked"
    assert "OUTLINE_PENDING_REWRITE_TASK" in {issue.code for issue in report.issues}


def test_blocks_three_hundred_year_direct_kinship_timeline_conflict() -> None:
    report = evaluate_chapter_outline_readiness(
        chapter_number=85,
        chapter_title="十七栋半卷不动",
        chapter_target_word_count=2200,
        chapter_metadata={},
        scene_cards=[
            {
                "scene_number": 1,
                "target_word_count": 2200,
                "purpose": {"story": "林渊发现父亲三百年前在井契上签过名。"},
                "metadata_json": {"methodology_contract": _methodology_contract()},
            }
        ],
    )

    assert report.verdict == "blocked"
    assert "OUTLINE_TIMELINE_LIFESPAN_CONFLICT" in {
        issue.code for issue in report.issues
    }


def test_blocks_configured_forbidden_timeline_anchor_pair() -> None:
    report = evaluate_chapter_outline_readiness(
        chapter_number=85,
        chapter_title="十七栋半卷不动",
        chapter_target_word_count=2200,
        chapter_metadata={
            "forbidden_timeline_anchor_pairs": [
                {"anchor": "三百年前旧契", "subjects": ["林正淳"]}
            ]
        },
        scene_cards=[
            {
                "scene_number": 1,
                "target_word_count": 2200,
                "purpose": {"story": "林正淳在三百年前旧契旁留下指印。"},
                "metadata_json": {"methodology_contract": _methodology_contract()},
            }
        ],
    )

    assert report.verdict == "blocked"
    assert "OUTLINE_TIMELINE_ANCHOR_CONFLICT" in {issue.code for issue in report.issues}
