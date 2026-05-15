from __future__ import annotations

# ruff: noqa: RUF001
import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services.chapter_causality_gate import (
    chapter_causality_report_to_dict,
    evaluate_chapter_causality_contract,
)

pytestmark = pytest.mark.unit


def test_chapter_causality_gate_accepts_flexible_action_contract() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "causal-good",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "血镜",
                    "goal": "苏砚在宿老封宅前读取铜镜残痕，并确认母亲旧案与青萝镇有关。",
                    "main_conflict": "宿老要在天黑前封宅，苏砚必须冒着身份暴露风险读取铜镜残痕。",
                    "hook_description": "铜镜裂开后，碎片指向姜家祖坟里的第二具棺材。",
                    "causal_contract": {
                        "chapter_function": "action",
                        "pressure": "宿老封宅倒计时压到天黑前。",
                        "protagonist_desire": "苏砚要拿到母亲旧案的第一条实证。",
                        "protagonist_choice": "他选择当众违令进入旧宅。",
                        "visible_action_or_reaction": "苏砚以砚台引出铜镜残痕。",
                        "resistance": "宿老和镇民阻止他触碰铜镜。",
                        "cost_or_tradeoff": "苏砚暴露了自己能听见器物怨声的秘密。",
                        "gain_or_reveal": "铜镜残痕证明母亲死前到过姜家祖坟。",
                        "state_change": "苏砚从无证据的怀疑变成握有第一条实证。",
                        "next_reader_desire": "读者想知道第二具棺材里是谁。",
                    },
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "investigation",
                            "time_label": "青萝镇旧宅天黑前",
                            "participants": ["苏砚", "宿老"],
                            "purpose": {
                                "story": "苏砚违令进入旧宅，以砚台引出铜镜残痕。",
                                "emotion": "抗拒与紧迫感同时上升。",
                            },
                            "entry_state": {"苏砚": {"evidence": "没有实证"}},
                            "exit_state": {"苏砚": {"evidence": "拿到姜家祖坟线索"}},
                        }
                    ],
                }
            ],
        }
    )

    report = evaluate_chapter_causality_contract(batch)
    payload = chapter_causality_report_to_dict(report)

    assert report.passed is True
    assert payload["passed"] is True
    assert payload["chapter_results"][0]["present_axes"]["pressure"] is True
    assert payload["chapter_results"][0]["present_axes"]["next_reader_desire"] is True


def test_chapter_causality_gate_accepts_reveal_chapter_without_fixed_beat_order() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "causal-reveal",
            "chapters": [
                {
                    "chapter_number": 8,
                    "title": "碑裂",
                    "goal": "宁尘对照碑纹与体内道种震动，确认宗门禁令掩盖了坠魂渊入口。",
                    "main_conflict": "执法弟子逼他交出拓片，宁尘必须在不暴露道种的前提下完成比对。",
                    "hook_description": "碑纹最后一笔亮起，指向今晚子时开启的坠魂渊侧门。",
                    "causal_contract": {
                        "chapter_function": "reveal",
                        "pressure": "执法弟子要求宁尘立刻交出拓片。",
                        "protagonist_desire": "宁尘想确认碑纹为何牵动体内道种。",
                        "protagonist_choice": "他选择假意配合，暗中完成最后一次比对。",
                        "visible_action_or_reaction": "宁尘用血遮住拓片缺口，逼碑纹显出暗线。",
                        "resistance": "执法弟子搜身，随时会发现道种反应。",
                        "cost_or_tradeoff": "他损失一张保命符，也被执法堂记名。",
                        "gain_or_reveal": "坠魂渊侧门将在今晚子时开启。",
                        "state_change": "宁尘从被动躲查变成握有主动潜入窗口。",
                        "next_reader_desire": "读者想看宁尘是否敢进坠魂渊侧门。",
                    },
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "reveal",
                            "time_label": "外门碑林黄昏",
                            "participants": ["宁尘", "执法弟子"],
                            "purpose": {
                                "story": "宁尘假意交出拓片前，用血遮住缺口逼出碑纹暗线。",
                                "emotion": "压迫感转为孤注一掷。",
                            },
                        }
                    ],
                }
            ],
        }
    )

    report = evaluate_chapter_causality_contract(batch)

    assert report.passed is True
    assert not report.findings


def test_chapter_causality_gate_lets_explicit_contract_override_legacy_generic_fields() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "causal-contract-overrides",
            "chapters": [
                {
                    "chapter_number": 9,
                    "title": "暗门",
                    "goal": "推动本章剧情发展。",
                    "main_conflict": "港口压力继续存在。",
                    "hook_description": "港口局势出现新的情况。",
                    "causal_contract": {
                        "chapter_function": "action",
                        "pressure": "封港倒计时只剩半炷香，沈砚必须进入暗门前确认信号源。",
                        "protagonist_desire": "沈砚要拿到信号源的第一手证据。",
                        "protagonist_choice": "沈砚选择骗开巡守，独自进入暗门。",
                        "visible_action_or_reaction": "沈砚撬开暗门并截住第一道信号回声。",
                        "resistance": "巡守搜门，封港令让他没有第二次尝试机会。",
                        "cost_or_tradeoff": "沈砚暴露了自己能听见信号回声的能力。",
                        "gain_or_reveal": "信号源证明港务官在封港前已经撒谎。",
                        "state_change": "沈砚从被动接任务变成握有反证的人。",
                        "next_reader_desire": "读者想知道港务官为什么提前撒谎。",
                    },
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "setup",
                            "time_label": "封港前半炷香",
                            "participants": ["沈砚"],
                            "purpose": {"story": "推进剧情。", "emotion": "情绪复杂。"},
                        }
                    ],
                }
            ],
        }
    )

    report = evaluate_chapter_causality_contract(batch)

    assert report.passed is True
    assert not report.findings


def test_chapter_causality_gate_accepts_specific_zh_question_contract() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "causal-zh-question",
            "chapters": [
                {
                    "chapter_number": 51,
                    "title": "城南旧事馆",
                    "goal": "林渊接到古怪委托，前往城南旧事馆鉴定一面清代铜镜。",
                    "main_conflict": "顾怀山的话语中暗藏试探，林渊需要在信任与警惕之间判断。",
                    "hook_description": "顾怀山打开锦盒，铜镜映出父亲失踪前的画面。",
                    "causal_contract": {
                        "chapter_function": "reveal",
                        "pressure": "镜中出现的父亲画面让林渊陷入震惊，需要判断这是陷阱还是真实线索。",
                        "protagonist_desire": "林渊想查明父亲失踪真相。",
                        "protagonist_choice": "林渊暂时信任顾怀山，同意帮他处理铜镜。",
                        "visible_action_or_reaction": "林渊用阴阳眼观察铜镜，发现镜背刻着林家封印纹路。",
                        "resistance": "顾怀山闪烁其词，几次试图岔开话题。",
                        "cost_or_tradeoff": "接受委托意味着林渊可能被卷入新的镜债。",
                        "gain_or_reveal": "林渊得知顾怀山与父亲三十年前经历过一次镜中局。",
                        "state_change": "林渊与顾怀山建立临时合作关系。",
                        "next_reader_desire": "这面铜镜为何会出现在这里？父亲三十年前经历了什么？",
                    },
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "investigation",
                            "participants": ["林渊", "顾怀山"],
                            "purpose": {
                                "story": "林渊观察铜镜并逼顾怀山说出父亲旧事。",
                                "emotion": "震惊转为警惕。",
                            },
                        }
                    ],
                }
            ],
        }
    )

    report = evaluate_chapter_causality_contract(batch)

    assert report.passed is True
    assert not report.findings


def test_chapter_causality_gate_accepts_short_specific_contract_values() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "causal-short-zh",
            "chapters": [
                {
                    "chapter_number": 77,
                    "title": "镜奴契约",
                    "goal": "林渊被周老板擒获，被迫观看师父受刑的画面。",
                    "main_conflict": "周老板要求林渊签署镜奴契约，成为激活地下古镜的钥匙。",
                    "hook_description": "周老板让林渊在契约和师父性命之间二选一。",
                    "causal_contract": {
                        "chapter_function": "dilemma",
                        "pressure": "师父命悬一线",
                        "protagonist_choice": "林渊选择假装签署契约。",
                        "visible_action_or_reaction": "林渊用镜主权限干扰契约。",
                        "resistance": "周老板早有防备",
                        "cost_or_tradeoff": "师父受重伤",
                        "gain_or_reveal": "苏婉宁带人赶到。",
                        "state_change": "局势逆转",
                        "next_reader_desire": "林渊能否救下师父？周老板会如何应对？",
                    },
                }
            ],
        }
    )

    report = evaluate_chapter_causality_contract(batch)

    assert report.passed is True
    assert not report.findings


def test_chapter_causality_gate_blocks_flat_reader_desire_chain() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "causal-bad",
            "chapters": [
                {
                    "chapter_number": 12,
                    "title": "空潮",
                    "goal": "沈砚继续处理港口局势。",
                    "main_conflict": "港口压力继续存在。",
                    "hook_description": "港口局势出现新的情况。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "transition",
                            "time_label": "港口次日",
                            "participants": ["沈砚"],
                            "purpose": {
                                "story": "沈砚思考港口局势。",
                                "emotion": "情绪复杂。",
                            },
                        }
                    ],
                }
            ],
        }
    )

    report = evaluate_chapter_causality_contract(batch)
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert {
        "CHAPTER_CAUSAL_PRESSURE_WEAK",
        "CHAPTER_CAUSAL_CHOICE_OR_ACTION_WEAK",
        "CHAPTER_CAUSAL_NEXT_DESIRE_WEAK",
    }.issubset(codes)
