import json
from pathlib import Path

import yaml

from bookengine.continuity_ledger import (
    continuity_findings,
    merge_continuity_delta,
    repair_continuity_ledger,
    writer_continuity_facts,
)
from bookengine.continuity_checkup import continuity_checkup
from bookengine.q1_extract import context
from tools.book_checkup import check_book


FIXTURE = json.loads((Path(__file__).parent / "fixtures/continuity53/eval52.json").read_text())


def bible():
    return {"characters": FIXTURE["characters"]}


def chapter(number):
    return FIXTURE["chapters"][str(number)]


def seeded_state():
    source = chapter(6)
    state = {}
    delta = {
        "characters": [
            {"name": "顾晴棣", "field": "life_status", "value": "alive", "quote": source},
            {
                "name": "顾晴棣",
                "field": "location",
                "value": "随队药车",
                "travel_status": "随行",
                "quote": source,
            },
            {
                "name": "顾晴棣",
                "field": "injury",
                "part": "手",
                "side": "右",
                "condition": "冻裂",
                "severity": "未愈",
                "quote": source,
            },
        ],
        "body_structures": [
            {
                "name": "雪沛宁",
                "form": "四足兽",
                "limb_count": 4,
                "limb_name": "腿",
                "quote": "雪沛宁是一头四足雪兽。",
            }
        ],
    }
    merged = merge_continuity_delta(state, delta, source + "\n\n雪沛宁是一头四足雪兽。", bible(), 6)
    memory = {"consumed_memories": [{"summary": "药棚守夜", "quote": chapter(34)}]}
    return merge_continuity_delta(merged, memory, chapter(34), bible(), 34)


def test_quote_grounded_ledger_records_people_body_and_consumed_memories():
    state = seeded_state()
    ledger = state["continuity_ledger"]

    sister = ledger["characters"]["顾晴棣"]
    assert sister["life_status"]["value"] == "alive"
    assert sister["location"]["travel_status"] == "随行"
    assert sister["injuries"] == [
        {
            "part": "手",
            "side": "右",
            "condition": "冻裂",
            "severity": "未愈",
            "start_chapter": 6,
            "chapter": 6,
            "quote": chapter(6),
        }
    ]
    assert ledger["characters"]["雪沛宁"]["body"]["limb_count"] == 4
    assert ledger["consumed_memories"][0]["summary"] == "药棚守夜"

    changed = merge_continuity_delta(
        state,
        {"characters": [{"name": "顾晴棣", "field": "life_status", "value": "dead", "quote": "她死了。"}]},
        "顾晴棣仍坐在车上。",
        bible(),
        35,
    )
    assert changed["continuity_ledger"]["characters"]["顾晴棣"]["life_status"]["value"] == "alive"


def test_real_model_delta_shape_keeps_grounded_injury_and_defaults_memory_owner_to_protagonist():
    text = "顾奕棣猛地扑过去，碎石擦着他的肩背砸进雪里。他再也想不起妹妹第一次叫他哥哥时的声音。"
    delta = {
        "characters": [
            {
                "name": "顾奕棣",
                "field": "injury",
                "value": "肩背碎石擦伤",
                "quote": "顾奕棣猛地扑过去，碎石擦着他的肩背砸进雪里。",
            }
        ],
        "consumed_memories": [
            {
                "name": "妹妹第一次叫哥哥的声音",
                "summary": "妹妹第一次叫哥哥的声音",
                "quote": "他再也想不起妹妹第一次叫他哥哥时的声音。",
            }
        ],
    }

    state = merge_continuity_delta({}, delta, text, bible(), 1)
    ledger = state["continuity_ledger"]

    assert ledger["characters"]["顾奕棣"]["injuries"][0]["part"] == "肩背"
    assert ledger["consumed_memories"][0]["name"] == "顾奕棣"


def test_writer_gets_short_natural_facts_without_internal_field_names():
    outline = {"number": 40, "scenes": [{"characters": ["顾晴棣", "雪沛宁"]}]}
    facts = writer_continuity_facts(seeded_state(), bible(), outline)
    joined = "\n".join(facts)

    assert "顾晴棣：随行，随队药车；右手冻裂，未愈" in joined
    assert "雪沛宁：四足兽，共四条腿" in joined
    assert "已消耗记忆，不能再次献祭或当作完整记忆想起：药棚守夜（第34章）" in joined
    assert all(term not in joined for term in ("life_status", "travel_status", "continuity_ledger", "limb_count"))

    sections = context(bible(), {}, {"number": 4}, outline, seeded_state(), [], [], "")
    section = dict(sections)["人物当前事实，不得违背"]
    assert "右手冻裂" in section and "药棚守夜" in section


def test_eval52_saved_lines_detect_injury_limb_location_life_and_memory_conflicts():
    state = seeded_state()
    expected = {
        27: "limb_count",
        31: "location_status",
        35: "consumed_memory",
        39: "injury_side",
        42: "life_status",
    }

    for number, kind in expected.items():
        findings = continuity_findings(chapter(number), state, bible(), number)
        assert kind in {row["kind"] for row in findings}, (number, findings)


def test_targeted_model_repair_rechecks_and_rolls_back_on_failure():
    state = seeded_state()
    text = chapter(39)
    requests = []

    def ask(_client, stage, _prompt, sections, _schema, **_kwargs):
        requests.append(stage)
        targets = json.loads(dict(sections)["确定性矛盾句"])
        assert targets[0]["kind"] == "injury_side"
        return {
            "findings": [],
            "edits": [{"id": targets[0]["id"], "text": "顾晴棣坐在药车上，冻伤的右手死死扣住车沿。"}],
        }

    def extract(*args, **kwargs):
        candidate = args[2]
        valid = "右手" in candidate
        return ({"state": {}, "continuity_delta": {}, "events": []}, not valid, 0)

    repaired, raw, failed, note = repair_continuity_ledger(
        None,
        "r2-ch39",
        text,
        {"state": {}, "events": []},
        False,
        state,
        bible(),
        {"number": 39},
        extract,
        ask,
    )
    assert repaired == "顾晴棣坐在药车上，冻伤的右手死死扣住车沿。"
    assert requests == ["r2-ch39-continuity-ledger"]
    assert raw["continuity_delta"] == {} and failed is False
    assert note["status"] == "repaired" and note["after"] == []

    rolled_back, _, _, rejected = repair_continuity_ledger(
        None,
        "r2-ch39-bad",
        text,
        {"state": {}, "events": []},
        False,
        seeded_state(),
        bible(),
        {"number": 39},
        extract,
        lambda *_args, **_kwargs: {"findings": [], "edits": []},
    )
    assert rolled_back == text
    assert rejected["status"] == "rolled_back"


def test_repeated_accident_is_reported_inside_ten_chapters_and_registered_in_checkup(tmp_path):
    chapters = [{"number": number, "text": chapter(number)} for number in (1, 3, 5, 7, 8)]
    result = continuity_checkup(bible(), chapters)

    assert result["counts"]["repeated_accidents"] >= 4
    assert result["repeated_accidents"][0]["window"] <= 10
    assert result["authority"] == "agent_editorial_not_human"

    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(bible(), allow_unicode=True))
    for row in chapters:
        (tmp_path / f"chapter-{row['number']:03d}.body.md").write_text(row["text"])
    report = check_book(tmp_path, source="draft")
    assert report["continuity_ledger"]["counts"]["repeated_accidents"] >= 4

    schemas = yaml.safe_load((Path(__file__).parents[1] / "data/q1_schemas.yaml").read_text())
    assert "10章内" in schemas["instructions"]["outlines"]
