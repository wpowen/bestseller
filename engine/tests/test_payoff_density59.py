"""Instruction 59 B5: two weighted title payoffs per stage and grounded review chapter IDs."""

import json

from bookengine.story_payoffs import review_story_payoffs
from bookengine.title_promises import (
    bind_title_contracts,
    enforce_promise_positions,
    ensure_reader_promise_contract,
    normalize_promise_references,
    structure_promise_issues,
    title_promise_checkup,
)


def design(title="借兽杀仇"):
    bible = {
        "title": title,
        "selling_points": ["主角借兽力清算仇敌"],
        "volumes": [
            {"number": 1, "start": 1, "end": 10, "antagonist": "岑烈"},
            {"number": 2, "start": 11, "end": 20, "antagonist": "白啸"},
        ],
        "antagonists": [
            {"name": "岑烈", "tier": 1, "stage": 1, "exit": 10},
            {"name": "白啸", "tier": 2, "stage": 2, "exit": 19},
        ],
    }
    bible = ensure_reader_promise_contract(bible, 20)
    primary = next(row["id"] for row in bible["reader_promises"] if row["primary"])
    for stage in bible["volumes"]:
        small = stage["start"] + (stage["end"] - stage["start"]) // 2
        large = stage["end"] if stage["end"] < 20 else 19
        stage["promise_payoffs"] = [
            {
                "promise_id": primary,
                "chapter": small,
                "scale": "小兑现",
                "action": "主角借兽力赢下一次局部争夺",
                "target": stage["antagonist"],
                "result": "夺回一处资源",
                "escalation": "阶段中段先兑现局部胜利",
            },
            {
                "promise_id": primary,
                "chapter": large,
                "scale": "大兑现",
                "action": "主角借兽力正面清算",
                "target": stage["antagonist"],
                "result": "终结本阶段压迫",
                "escalation": "阶段末兑现不可逆胜负",
            },
        ]
        stage["opponent_resolution"] = {
            "name": stage["antagonist"],
            "type": "被擒",
            "chapter": large,
            "outcome": "失去继续作恶能力",
        }
    bible["volumes"][-1]["ending_contract"] = {
        "promise_response": "借兽力完成清算",
        "growth_dimension": "身份",
        "before": "矿镇弃徒",
        "after": "雪原领路人",
        "new_burden_or_mystery": "",
    }
    return bible


def test_each_stage_needs_distinct_middle_small_and_late_large_payoffs():
    bible = design(title="借兽清算")
    bible["volumes"][0]["promise_payoffs"] = bible["volumes"][0]["promise_payoffs"][-1:]
    assert any("两个兑现点" in issue for issue in structure_promise_issues(bible, 20))

    bible = design(title="借兽清算")
    bible["volumes"][0]["promise_payoffs"][1]["chapter"] = bible["volumes"][0]["promise_payoffs"][0]["chapter"]
    assert any("章号不同" in issue for issue in structure_promise_issues(bible, 20))

    bible = design(title="借兽清算")
    bible["volumes"][0]["promise_payoffs"][0]["chapter"] = 7
    assert title_promise_checkup(bible)["payoff_density"][0]["status"] == "invalid_positions"

    assert not [issue for issue in structure_promise_issues(design(title="借兽清算"), 20) if "payoff" in issue]


def test_planning_projects_late_small_payoffs_back_to_each_stage_midpoint():
    bible = design(title="借兽清算")
    bible["volumes"][0]["promise_payoffs"][0]["chapter"] = 7
    bible["volumes"][1]["promise_payoffs"][0]["chapter"] = 16

    repaired = enforce_promise_positions(bible, 20)

    assert [stage["promise_payoffs"][0]["chapter"] for stage in repaired["volumes"]] == [5, 15]
    assert not [issue for issue in structure_promise_issues(repaired, 20) if "兑现" in issue or "payoff" in issue]
    assert bible["volumes"][0]["promise_payoffs"][0]["chapter"] == 7


def test_only_validated_schedule_can_put_a_payoff_or_resolution_on_an_outline():
    bible = design(title="借兽清算")
    batch = [
        {
            "number": 20,
            "type": "揭秘",
            "reader_promise_id": "model-invented",
            "promise_payoff": {"action": "并非计划内兑现"},
            "opponent_resolution": {"name": "白啸", "type": "追踪者", "outcome": "逼退"},
        }
    ]

    bound = bind_title_contracts(bible, batch)[0]

    assert "reader_promise_id" not in bound
    assert "promise_payoff" not in bound
    assert "opponent_resolution" not in bound


def test_scheduled_opponent_resolution_makes_the_planned_chapter_combat():
    bible = design(title="借兽清算")
    chapter = bible["volumes"][0]["opponent_resolution"]["chapter"]

    bound = bind_title_contracts(bible, [{"number": chapter, "type": "收获", "rhythm_type": "收获"}])[0]

    assert bound["opponent_resolution"]["name"] == "岑烈"
    assert bound["type"] == "对抗" and bound["rhythm_type"] == "对抗"


def test_scheduled_combat_payoff_cannot_also_carry_a_relationship_contract():
    bible = design(title="借兽清算")
    chapter = bible["volumes"][0]["promise_payoffs"][0]["chapter"]
    planned = {
        "number": chapter,
        "type": "情感",
        "rhythm_type": "情感",
        "relationship_change": {
            "people": ["顾立谦", "霜团"],
            "cause": "顾立谦坦白代价",
            "before": "霜团不信任他",
            "after": "霜团愿意同行",
        },
    }

    bound = bind_title_contracts(bible, [planned])[0]

    assert bound["promise_payoff"]["scale"] == "小兑现"
    assert bound["type"] == "对抗" and "relationship_change" not in bound


def test_literal_kill_in_title_requires_at_least_one_opponent_killed():
    bible = design()
    assert any("至少一个阶段反派被杀" in issue for issue in structure_promise_issues(bible, 20))

    bible["volumes"][-1]["opponent_resolution"]["type"] = "被杀"
    assert not any("至少一个阶段反派被杀" in issue for issue in structure_promise_issues(bible, 20))


def test_model_p01_alias_binds_to_primary_promise_before_validation_and_checkup():
    bible = design(title="借兽清算")
    for stage in bible["volumes"]:
        for payoff in stage["promise_payoffs"]:
            payoff["promise_id"] = "P01"

    normalized = normalize_promise_references(bible)
    primary = next(row["id"] for row in normalized["reader_promises"] if row["primary"])

    assert {payoff["promise_id"] for stage in normalized["volumes"] for payoff in stage["promise_payoffs"]} == {primary}
    assert all(row["status"] == "met" for row in title_promise_checkup(bible)["payoff_density"])


def test_story_payoff_review_schema_uses_current_chapter_and_wrong_chapter_stays_unknown():
    outline = {
        "number": 10,
        "reader_promise_id": "reader-promise-1",
        "promise_payoff": {"action": "顾怀瑾救回顾雪", "target": "曹砺", "result": "夺回通行牌"},
    }
    text = "顾怀瑾救回顾雪，当众夺回通行牌。"
    schemas = []

    def ask(client, key, instruction, sections, schema, **kwargs):
        schemas.append(json.loads(schema))
        item = json.loads(dict(sections)["本章必须发生的事"])[0]
        return {
            "items": [
                {
                    "id": item["id"],
                    "status": "fulfilled",
                    "evidence": [{"chapter": 1, "quote": text}],
                    "reason": "事件已发生但章号复制错误",
                }
            ]
        }

    note = review_story_payoffs(None, "r2-ch10", text, outline, ask)

    assert schemas[0]["items"][0]["evidence"][0]["chapter"] == 10
    assert note["items"][0]["status"] == "unknown"
