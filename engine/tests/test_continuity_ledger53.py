import json
from pathlib import Path

from bookengine.continuity_ledger import merge_continuity_delta


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
