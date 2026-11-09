"""Reader-title promise planning regressions for INBOX item 55."""

from copy import deepcopy

import yaml

from bookengine.structure_planning import structure_issues
from bookengine.title_promises import (
    bind_title_contracts,
    ensure_reader_promise_contract,
    extract_reader_promises,
    normalize_promise_references,
    planning_promise_issues,
    writer_title_contract,
)
from tools.book_checkup import check_book


def _design(total=20):
    stages = [
        {
            "number": 1,
            "start": 1,
            "end": 10,
            "location": "矿镇",
            "antagonist": "烛无咎",
            "goal": "救出妹妹",
            "climax": "雪兽正面重创烛无咎",
            "exit_chapter": 10,
        },
        {
            "number": 2,
            "start": 11,
            "end": total,
            "location": "兽谷",
            "antagonist": "白啸",
            "goal": "终结恶契",
            "climax": "雪兽正面击杀白啸",
            "exit_chapter": total,
        },
    ]
    return {
        "title": "《我拿回忆喂兽，兽力替我杀人》",
        "selling_points": ["每次借兽力都永久失去一段温暖回忆"],
        "volumes": stages,
        "antagonists": [
            {"name": "烛无咎", "tier": 1, "stage": 1, "exit": 10},
            {"name": "白啸", "tier": 2, "stage": 2, "exit": total},
        ],
    }


def _valid_contract(total=20):
    bible = ensure_reader_promise_contract(_design(total), total)
    primary = next(p for p in bible["reader_promises"] if p["primary"])
    for stage in bible["volumes"]:
        resolution_chapter = stage["end"] if stage["number"] == 1 else stage["end"] - 1
        stage["promise_payoffs"] = [
            {
                "promise_id": primary["id"],
                "chapter": resolution_chapter,
                "action": "顾奕棣驱使雪沛宁正面扑杀",
                "target": stage["antagonist"],
                "result": "击杀" if stage["number"] == 2 else "重创",
                "escalation": f"第{stage['number']}阶段升级兑现",
            }
        ]
        stage["opponent_resolution"] = {
            "name": stage["antagonist"],
            "type": "被杀" if stage["number"] == 2 else "被废",
            "chapter": resolution_chapter,
            "outcome": "失去继续作恶能力",
        }
        bible["antagonists"][stage["number"] - 1]["exit"] = resolution_chapter
    bible["volumes"][-1]["ending_contract"] = {
        "promise_response": "雪沛宁替顾奕棣清算白啸，喂忆借力的选择由二者共享",
        "growth_dimension": "身份",
        "before": "顾家弃卒",
        "after": "被雪原公开承认的终契者",
        "new_burden_or_mystery": "",
    }
    return bible


def test_eval52_title_extracts_specific_reader_promises():
    promises = extract_reader_promises(_design())

    assert [p["text"] for p in promises] == ["喂回忆换兽力", "兽力替我杀人"]
    assert [p["primary"] for p in promises] == [False, True]


def test_model_promise_labels_migrate_to_stable_primary_id_without_changing_content():
    bible = ensure_reader_promise_contract(_design(), 20)
    bible["volumes"][0]["promise_payoffs"] = [
        {
            "promise_id": "primary:兽力替我杀人·主承诺",
            "chapter": 8,
            "action": "雪沛宁正面扑杀",
            "target": "烛无咎",
            "result": "重创",
            "escalation": "首次正面清算",
        }
    ]

    result = normalize_promise_references(bible)

    assert result["volumes"][0]["promise_payoffs"][0]["promise_id"] == "reader-promise-2"
    assert result["volumes"][0]["promise_payoffs"][0]["action"] == "雪沛宁正面扑杀"
    assert bible["volumes"][0]["promise_payoffs"][0]["promise_id"] == "primary:兽力替我杀人·主承诺"

    bible["volumes"][0]["promise_payoffs"][0]["promise_id"] = "main_promise"
    assert normalize_promise_references(bible)["volumes"][0]["promise_payoffs"][0]["promise_id"] == ("reader-promise-2")


def test_structure_requires_each_stage_payoff_clear_opponent_exit_and_closed_ending():
    incomplete = ensure_reader_promise_contract(_design(), 20)
    issues = "\n".join(structure_issues(incomplete, 20))

    assert "reader promise" in issues
    assert "opponent_resolution" in issues
    assert "ending_contract" in issues

    complete = _valid_contract()
    assert structure_issues(complete, 20) == []

    broken = deepcopy(complete)
    broken["volumes"][-1]["opponent_resolution"]["chapter"] = 20
    broken["antagonists"][-1]["exit"] = 20
    broken["volumes"][-1]["ending_contract"]["new_burden_or_mystery"] = "新的幕后人现身"
    issues = "\n".join(structure_issues(broken, 20))
    assert "末章前" in issues
    assert "新污名或新谜团" in issues


def test_outline_contract_requires_scheduled_payoff_resolution_and_final_growth():
    bible = _valid_contract()
    empty = [{"number": number, "scenes": []} for number in range(1, 21)]
    issues = planning_promise_issues(bible, empty)
    assert {issue["kind"] for issue in issues} == {
        "reader_promise_payoff",
        "opponent_resolution",
        "ending_promise_growth",
    }

    chapters = deepcopy(empty)
    for stage in bible["volumes"]:
        payoff = stage["promise_payoffs"][0]
        row = chapters[payoff["chapter"] - 1]
        row["reader_promise_id"] = payoff["promise_id"]
        row["promise_payoff"] = {k: payoff[k] for k in ("action", "target", "result", "escalation")}
        resolution = stage["opponent_resolution"]
        row["opponent_resolution"] = {
            "name": resolution["name"],
            "type": resolution["type"],
            "outcome": resolution["outcome"],
        }
    chapters[-1]["ending_promise_response"] = bible["volumes"][-1]["ending_contract"]

    assert planning_promise_issues(bible, chapters) == []


def test_validated_stage_contracts_are_rebound_after_outline_fallback():
    bible = _valid_contract()
    fallback = [
        {
            "number": number,
            "reader_promise_id": "",
            "promise_payoff": {"action": "", "target": "", "result": "", "escalation": ""},
            "opponent_resolution": {"name": "", "type": "", "outcome": ""},
            "ending_promise_response": {
                "promise_response": "",
                "growth_dimension": "",
                "before": "",
                "after": "",
            },
        }
        for number in range(1, 21)
    ]

    rebound = bind_title_contracts(bible, fallback)

    assert planning_promise_issues(bible, rebound) == []
    assert planning_promise_issues(bible, fallback)
    assert rebound[-1]["ending_promise_response"] == bible["volumes"][-1]["ending_contract"]


def test_writer_receives_positive_payoff_named_exit_and_closed_growth_in_existing_field():
    bible = _valid_contract()
    stage = bible["volumes"][-1]
    payoff = stage["promise_payoffs"][0]
    outline = {
        "number": payoff["chapter"],
        "hook": "白啸挣脱束缚，带着新线索离开",
        "reader_promise_id": payoff["promise_id"],
        "promise_payoff": payoff,
        "opponent_resolution": stage["opponent_resolution"],
        "ending_promise_response": stage["ending_contract"],
    }

    result = writer_title_contract(outline)
    instructions = "\n".join(result["must_respond"])

    assert "正面兑现" in instructions and "白啸" in instructions and "被杀" in instructions
    assert "顾家弃卒" in instructions and "不添新污名" in instructions
    assert "白啸已被杀" in result["hook"] and "不得让其脱身" in result["hook"]
    assert "must_respond" not in outline


def test_checkup_lists_unknown_legacy_schedule_without_inventing_fulfillment(tmp_path):
    bible = _design()
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(bible, allow_unicode=True, sort_keys=False))
    (tmp_path / "chapter-001.body.md").write_text("雪沛宁驮着伤员离开矿道。")

    result = check_book(tmp_path)["title_promises"]

    assert [p["text"] for p in result["promises"]] == ["喂回忆换兽力", "兽力替我杀人"]
    assert result["scheduled_payoffs"] == []
    assert result["status"] == "Unknown"
    assert all(row["resolution_type"] == "Unknown" for row in result["opponent_resolutions"])
