"""Small copy-safe views used only in the prose writer prompt."""

import json


def previous_outcome_facts(state, history, chapter_rows):
    """Expose saved consequences without exposing copyable previous prose."""
    facts = {}
    for source in (history or [], chapter_rows or []):
        iterable = source.values() if isinstance(source, dict) else source
        for row in iterable:
            if not isinstance(row, dict):
                continue
            facts.update(
                {
                    key: row.get(key)
                    for key in ("summary", "events", "payoff", "gains", "costs", "advances", "demotion_event")
                    if row.get(key) not in (None, "", [])
                }
            )
    boundary = {
        key: state.get(key) for key in ("end_location", "present", "end_pov") if state.get(key) not in (None, "", [])
    }
    if state.get("dashboard"):
        boundary["dashboard"] = state["dashboard"]
    return json.dumps(
        {
            "rule": "上一章已完成；只承接这些结局事实，不复述、改写或引用上一章末段原句。",
            "outcome": facts,
            "boundary": boundary,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def selected_card_writer_view(bible):
    card = bible.get("selected_card") if isinstance(bible.get("selected_card"), dict) else {}
    points = [
        point
        for point in bible.get("selected_card_rule_points", [])
        if isinstance(point, str) and point.strip() and len(point.strip()) <= 16
    ]
    return {
        key: card.get(key)
        for key in ("title", "premise", "opening", "hook")
        if isinstance(card.get(key), str) and card[key].strip()
    } | {
        "核心规则要点": points,
        "写法约束": "只据要点设计本章动作与后果，禁止照搬选题卡规则原句或把要点拼回说明句。",
    }


def power_writer_view(bible):
    power = bible.get("power") if isinstance(bible.get("power"), dict) else {}
    return {
        key: power.get(key) for key in ("name", "object") if isinstance(power.get(key), str) and power[key].strip()
    } | {
        "rule_points": selected_card_writer_view(bible)["核心规则要点"],
        "rule": "禁止照搬选题卡规则原句；用本章具体操作和当场结果体现，限制只在本章剧情用得上时出现。",
    }
