from __future__ import annotations

import json
from pathlib import Path

from bestseller.services.outline_semantic_gate import evaluate_outline_semantic_gate


def _fixture() -> dict:
    path = Path(__file__).parents[1] / "fixtures" / "outline_semantic_bad_book.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_bad_book_is_blocked_with_actionable_chapter_evidence() -> None:
    report = evaluate_outline_semantic_gate(_fixture())
    codes = {finding.code for finding in report.findings}

    assert report.promotion_allowed is False
    assert report.repairable is False
    assert {
        "OUTLINE_IDENTITY_MISMATCH",
        "OUTLINE_TONE_MISMATCH",
        "OUTLINE_WORD_BUDGET_MISMATCH",
        "OUTLINE_GOAL_DEGENERATE",
        "OUTLINE_OPENING_DEGENERATE",
        "OUTLINE_META_LANGUAGE",
        "OUTLINE_PLACEHOLDER_TITLE",
        "OUTLINE_DUPLICATE_EVENT_SIGNATURE",
        "OUTLINE_CONTRADICTORY_TRANSFER_ACCEPT_RETURN",
        "OUTLINE_LEGACY_NUMERIC_STATE",
    } <= codes
    assert any(finding.chapter == 1 for finding in report.findings)
    assert all(isinstance(finding.evidence, dict) for finding in report.findings)


def test_clean_outline_is_promotable() -> None:
    report = evaluate_outline_semantic_gate(
        {
            "story_spine": {
                "title": "灰港回声",
                "protagonist": "林岑",
                "genre": "悬疑",
                "tone": "阴郁",
            },
            "commercial_brief": {
                "title": "灰港回声",
                "protagonist": "林岑",
                "genre": "悬疑",
                "tone": "阴郁",
            },
            "identity_manifest": {
                "title": "灰港回声",
                "protagonist": "林岑",
                "genre": "悬疑",
                "tone": "阴郁",
            },
            "chapters": [
                {
                    "chapter_number": 1,
                    "chapter_title": "南桥下面",
                    "chapter_goal": "林岑拿到桥下监控",
                    "conflict": "保安烧掉证据前截住他",
                    "opening": "凌晨两点，南桥下传来三声敲击",  # noqa: RUF001
                    "estimated_chapter_words": 2600,
                    "event_signature": "截住保安",
                },
                {
                    "chapter_number": 2,
                    "chapter_title": "三声敲击",
                    "chapter_goal": "林岑确认监控缺失一帧",
                    "conflict": "陌生人要求交出备份",
                    "opening": "敲击声从排水口再次响起",
                    "estimated_chapter_words": 2600,
                    "event_signature": "确认缺帧",
                },
            ],
        }
    )
    assert report.promotion_allowed is True
    assert report.findings == ()


def test_project_specific_long_chapter_target_is_not_compared_to_hidden_defaults() -> None:
    report = evaluate_outline_semantic_gate(
        {
            "chapters": [
                {
                    "chapter_number": 1,
                    "chapter_title": "长夜启航",
                    "chapter_goal": "沈砚在封锁前抢到星图原件",
                    "main_conflict": "巡航署封锁航道并追查星图持有人",
                    "opening_situation": "最后一艘巡航艇正在解除泊锁",
                    "target_word_count": 6667,
                }
            ]
        }
    )

    assert "OUTLINE_WORD_BUDGET_MISMATCH" not in {
        finding.code for finding in report.findings
    }


def test_missing_event_signature_is_not_stringified_as_duplicate_sentinel() -> None:
    report = evaluate_outline_semantic_gate(
        {
            "chapters": [
                {
                    "chapter_number": number,
                    "chapter_title": f"第{number}章",
                    "chapter_goal": f"姬衡完成第{number}步营救",
                    "opening_situation": f"第{number}处宫门已闭",
                    "main_conflict": f"第{number}路追兵逼近",
                }
                for number in range(1, 4)
            ]
        }
    )

    codes = {finding.code for finding in report.findings}
    assert "OUTLINE_DUPLICATE_EVENT_SIGNATURE" not in codes
    assert "OUTLINE_ADJACENT_REPETITION" not in codes


def test_evaluator_errors_fail_closed() -> None:
    report = evaluate_outline_semantic_gate({"chapters": 42})
    assert report.promotion_allowed is False
    assert report.findings[0].code == "OUTLINE_SEMANTIC_EVALUATOR_ERROR"


def test_runtime_manifest_and_cross_chapter_percent_regression_are_blocked() -> None:
    report = evaluate_outline_semantic_gate(
        {
            "target_word_count": 13_000,
            "story_spine": {"who": "陆沉在禁区求生"},
            "commercial_brief": {},
            "identity_manifest": [
                {"role": "protagonist", "canonical_name": "裴野"},
            ],
            "chapters": [
                {
                    "chapter_number": 1,
                    "chapter_title": "门外三声",
                    "chapter_goal": "裴野确认门外来人的身份",
                    "main_conflict": "来人要求他立刻打开柴房",
                    "opening_situation": "门闩在第三声敲击后松了一寸",
                    "target_word_count": 2_400,
                    "metadata": {"state_change": "声纹复刻度突破62%临界"},
                },
                {
                    "chapter_number": 2,
                    "chapter_title": "雪夜以前",
                    "chapter_goal": "裴野在巡查前藏好旧账",
                    "main_conflict": "巡查司提前封住柴房后门",
                    "opening_situation": "巡查令贴上后门时雪刚落下",
                    "target_word_count": 2_400,
                    "metadata": {"state_change": "声纹复刻度逼近55%"},
                },
            ],
        }
    )
    codes = {finding.code for finding in report.findings}
    assert "OUTLINE_IDENTITY_MISMATCH" in codes
    assert "OUTLINE_STATE_REGRESSION" in codes
    assert "OUTLINE_WORD_BUDGET_MISMATCH" in codes


def test_live_rolling_batch_replay_and_degenerate_contract_are_blocked() -> None:
    repeated_payload = "北坡哨位今夜换一个值守弟子"
    copied_fallback = "母亲把最后一圈麻绳系上，主角必须立刻找出再留一月的理由"
    report = evaluate_outline_semantic_gate(
        {
            "chapters": [
                {
                    "chapter_number": 4,
                    "chapter_title": "喂债",
                    "chapter_goal": f"裴野必须卖出‘{repeated_payload}’这条情报",
                    "opening_situation": "深夜药圃，喂债倒计时只剩一刻",
                    "main_conflict": "裴野必须决定是否把无辜弟子拖进债局",
                    "hook_type": "sudden_reveal",
                    "metadata": {
                        "key_reveals": ["禁荒用名字结算债务"],
                        "causal_contract": {
                            "pressure": "倒计时逼近",
                            "resistance": "弟子背着家里寄来的粮袋",
                            "protagonist_choice": "裴野改用铜哨示警",
                        },
                    },
                },
                {
                    "chapter_number": 5,
                    "chapter_title": "阶上",
                    "chapter_goal": "裴野要让母亲暂缓离开宗门",
                    "opening_situation": "裴氏（母亲）正在厢房系紧包袱",
                    "main_conflict": "本章在卷中负责让主角看见最大压力源",
                    "hook_type": "sudden_reveal",
                    "metadata": {
                        "causal_contract": {
                            "pressure": copied_fallback,
                            "resistance": copied_fallback,
                            "cost_or_tradeoff": copied_fallback,
                            "state_change": "母亲把包袱改成活结",
                        }
                    },
                },
                {
                    "chapter_number": 6,
                    "chapter_title": "哨债",
                    "chapter_goal": f"本章交付燃：裴野再次卖出‘{repeated_payload}’",
                    "opening_situation": "卯时前，纪木在登记堂后门等他",
                    "main_conflict": "裴野必须把情报交给纪木又不暴露来源",
                    "hook_type": "sudden_reveal",
                    "metadata": {"chapter_information_introduced": ["纪木被列入排查"]},
                },
            ]
        }
    )

    codes = {finding.code for finding in report.findings}
    assert report.promotion_allowed is False
    assert {
        "OUTLINE_META_LANGUAGE",
        "OUTLINE_ROLE_SCHEMA_LEAK",
        "OUTLINE_CAUSAL_CONTRACT_DEGENERATE",
        "OUTLINE_INFORMATION_CONTRACT_GAP",
        "OUTLINE_HOOK_TYPE_STREAK",
        "OUTLINE_REUSED_PAYLOAD_ANCHOR",
    } <= codes
