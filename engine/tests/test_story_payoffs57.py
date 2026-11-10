"""Instruction 57: scheduled payoffs must become grounded prose, not planning-only metadata."""

import json

import pytest
import yaml

from bookengine.story_payoffs import repair_story_payoffs, review_story_payoffs
from bookengine.title_promises import writer_title_contract


def outline():
    return {
        "number": 8,
        "reader_promise_id": "reader-promise-1",
        "promise_payoff": {
            "action": "顾立谦带矿民截断岑烈退路",
            "target": "岑烈",
            "result": "夺回粮车与驿路控制点",
            "escalation": "第一次让矿民共同掌路",
        },
        "opponent_resolution": {"name": "岑烈", "type": "被擒", "outcome": "失去粮道与继续作恶能力"},
    }


def test_due_payoff_and_opponent_exit_are_reviewed_together_with_grounded_quotes():
    text = "矿民守住坡口。\n\n岑烈的身影很快消失在风雪中。"
    calls = []

    def ask(client, key, instruction, sections, schema, **kwargs):
        items = json.loads(dict(sections)["本章必须发生的事"])
        calls.append((key, items))
        return {"items": [{"id": item["id"], "status": "missing", "evidence": []} for item in items]}

    note = review_story_payoffs(None, "ch8", text, outline(), ask)

    assert note["status"] == "unfulfilled"
    assert len(calls) == 1 and {item["kind"] for item in calls[0][1]} == {"promise", "opponent_exit"}
    assert all(item["status"] == "missing" for item in note["items"])


def test_missing_payoff_gets_one_tail_rewrite_and_is_adopted_only_after_recheck():
    original = "矿民守住坡口。\n\n岑烈的身影很快消失在风雪中。"
    replacement = "矿民合力截断雪梁退路，夺回粮车与驿路控制点；岑烈被猎索捆住押下，粮道和人手尽失。"
    calls = []

    def ask(client, key, instruction, sections, schema, **kwargs):
        calls.append(key)
        if key == "ch8-prose-review":
            items = json.loads(dict(sections)["本章必须发生的事"])
            return {"items": [{"id": item["id"], "status": "missing", "evidence": []} for item in items]}
        if key.endswith("-tail-repair"):
            allowed = json.loads(dict(sections)["只允许修改的结尾段"])
            return {"edits": [{"id": allowed[-1]["id"], "original": allowed[-1]["original"], "text": replacement}]}
        items = json.loads(dict(sections)["本章必须发生的事"])
        return {
            "items": [
                {"id": item["id"], "status": "fulfilled", "evidence": [{"chapter": 8, "quote": replacement}]}
                for item in items
            ]
        }

    def extract(*args, **kwargs):
        return {"events": [], "summary": "矿民夺路并擒住岑烈"}, False, 0

    changed, raw, failed, note = repair_story_payoffs(
        None, "ch8", original, {"events": []}, False, {}, {}, outline(), extract, ask
    )

    assert changed.endswith(replacement)
    assert raw["summary"] == "矿民夺路并擒住岑烈" and failed is False
    assert note["status"] == "repaired"
    assert sum(key.endswith("-tail-repair") for key in calls) == 1


@pytest.mark.parametrize("grounded", [False, True])
def test_payoff_repair_rolls_back_when_quote_or_final_state_recheck_fails(grounded):
    original = "矿民守住坡口。\n\n岑烈的身影很快消失在风雪中。"
    replacement = "矿民截断退路，岑烈被擒，粮道易主。"

    def ask(client, key, instruction, sections, schema, **kwargs):
        if key == "ch8-prose-review":
            items = json.loads(dict(sections)["本章必须发生的事"])
            return {"items": [{"id": item["id"], "status": "missing", "evidence": []} for item in items]}
        if key.endswith("-tail-repair"):
            allowed = json.loads(dict(sections)["只允许修改的结尾段"])
            return {"edits": [{"id": allowed[-1]["id"], "original": allowed[-1]["original"], "text": replacement}]}
        items = json.loads(dict(sections)["本章必须发生的事"])
        quote = replacement if grounded else "正文里没有这句话"
        return {
            "items": [
                {"id": item["id"], "status": "fulfilled", "evidence": [{"chapter": 8, "quote": quote}]}
                for item in items
            ]
        }

    result = repair_story_payoffs(
        None,
        "ch8",
        original,
        {"events": []},
        False,
        {},
        {},
        outline(),
        lambda *args, **kwargs: ({"events": []}, grounded, 0),
        ask,
    )

    assert result[:3] == (original, {"events": []}, False)
    assert result[-1]["status"] == "rolled_back"


def test_writer_receives_natural_must_happen_language_without_internal_field_names():
    written = writer_title_contract(outline())
    prompt = "\n".join(written["must_respond"])

    assert "顾立谦带矿民截断岑烈退路" in prompt and "岑烈被擒" in prompt
    assert "promise_payoff" not in prompt and "opponent_resolution" not in prompt
    assert "书名承诺正面兑现" not in prompt


@pytest.mark.parametrize(
    "text,expected",
    [
        ("岑烈又带人追杀矿民。", True),
        ("岑烈跑了，他们却没跟。", True),
        ("岑烈被绑在囚车里，双手扣着镣铐。", False),
        ("众人提起岑烈时，火堆旁一时无声。", False),
    ],
)
def test_retired_opponent_may_be_captive_or_mentioned_but_not_act_normally(text, expected):
    from bookengine.retired_opponents import active_opponent_hits

    bible = {
        "antagonists": [{"name": "岑烈", "exit": 8}],
        "characters": [{"name": "岑烈", "role": "antagonist"}],
    }
    assert bool(active_opponent_hits(text, bible, 9)) is expected


def test_checkup_flags_scheduled_exit_missing_from_eval_style_prose(tmp_path):
    from tools.book_checkup import check_book

    bible = {
        "title": "夺回驿路",
        "reader_promises": [{"id": "reader-promise-1", "text": "夺回驿路", "primary": True}],
        "reader_promise_contract_version": 1,
        "reader_promise_total": 10,
        "volumes": [
            {
                "number": 1,
                "start": 1,
                "end": 10,
                "antagonist": "岑烈",
                "promise_payoffs": [dict(outline()["promise_payoff"], promise_id="reader-promise-1", chapter=8)],
                "opponent_resolution": dict(outline()["opponent_resolution"], chapter=8),
            }
        ],
        "antagonists": [{"name": "岑烈", "exit": 8}],
        "characters": [{"name": "岑烈", "role": "antagonist"}],
    }
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(bible, allow_unicode=True), encoding="utf-8")
    (tmp_path / "chapter-008.body.md").write_text("岑烈的身影很快消失在风雪中。", encoding="utf-8")

    prose = check_book(tmp_path)["title_promises"]["prose_contracts"]

    exit_row = next(row for row in prose if row["kind"] == "opponent_exit")
    assert exit_row["chapter"] == 8 and exit_row["result"] == "missing"
    assert exit_row["status"] == "Inference"
