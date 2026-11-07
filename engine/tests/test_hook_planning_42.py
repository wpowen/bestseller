from types import SimpleNamespace

from bookengine import q1_plan
from bookengine.hook_momentum import apply_ending_contract, diagnose_ending, schedule_ending_types


def test_schedule_ending_types_rotates_with_stage_limits_and_flat_only_at_end():
    slots = schedule_ending_types(1, 12, stage_end=12)
    types = [row["ending_type"] for row in slots]
    assert all(left != right for left, right in zip(types, types[1:]))
    assert all(types.count(kind) <= 2 for kind in set(types))
    assert "平收" not in types

    allowed_flat = schedule_ending_types(1, 12, stage_end=12, prefer_flat_close=True)
    assert [row["number"] for row in allowed_flat if row["ending_type"] == "平收"] == [12]


def test_apply_ending_contract_adds_specific_writer_instruction_and_repairs_bad_types():
    chapters = [
        {
            "number": 1,
            "title": "矿洞",
            "ending_type": "平收",
            "ending_content": "",
            "hook": "",
            "scenes": [
                {
                    "where": "矿洞石门",
                    "characters": ["顾冬岚"],
                    "obstacle": "石门后传来陌生呼吸" * 20,
                    "choice": "顾冬岚伸手推门",
                }
            ],
        },
        {
            "number": 2,
            "title": "来客",
            "ending_type": "迫近危险",
            "ending_content": "追兵堵住唯一出口",
            "hook": "追兵堵住唯一出口",
            "scenes": [],
        },
    ]
    result = apply_ending_contract(chapters, stage_end=2)
    assert result[0]["ending_type"] != "平收"
    assert result[0]["ending_content"]
    assert "章末留下什么" in result[0]["hook"]
    assert "具体内容" in result[0]["hook"]
    assert result[0]["ending_type"] != result[1]["ending_type"]
    assert result[0]["ending_type"] in diagnose_ending(result[0]["ending_content"])["types"]
    assert result[1]["ending_type"] in diagnose_ending(result[1]["ending_content"])["types"]
    assert all(len(row["ending_content"]) <= 160 for row in result)


def test_normalized_legacy_outline_cache_receives_new_ending_contract(monkeypatch):
    cached = {
        "normalized": True,
        "structure_checks_version": q1_plan.STRUCTURE_CHECKS_VERSION,
        "plot_history": [],
        "chapters": [
            {"number": 1, "title": "矿洞", "hook": "追兵堵住出口", "scenes": []},
            {"number": 2, "title": "雪线", "hook": "铜铃无风自鸣", "scenes": []},
        ],
    }
    monkeypatch.setattr(q1_plan, "historical_outlines", lambda *args: [])
    monkeypatch.setattr(q1_plan, "terminal_window", lambda *args: cached)
    monkeypatch.setattr(q1_plan, "solution_issues", lambda *args: [])
    monkeypatch.setattr(q1_plan, "ability_issues", lambda *args: [])
    monkeypatch.setattr(q1_plan, "rhythm_issues", lambda *args: [])
    monkeypatch.setattr(q1_plan, "complete_transitions", lambda chapters, prior: chapters)

    result = q1_plan.outlines(
        SimpleNamespace(out=None),
        {},
        {},
        {},
        {"number": 1, "start": 1, "end": 2},
        1,
        2,
        {},
        [],
        [],
        [],
    )
    assert [row["ending_type"] for row in result] == ["迫近危险", "反常物件"]
    assert all("章末留下什么" in row["hook"] for row in result)
    assert all(row["ending_type"] in diagnose_ending(row["ending_content"])["types"] for row in result)
