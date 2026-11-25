"""Small copy-safe views used only in the prose writer prompt."""

import json
import re

from .longmemory import compact_state


_UNCERTAIN_HOLDER = re.compile(r"[不没未]|以为|以往|想起|如果|假如|曾经|打算|交给|递给|放下|扔下|丢了")
_DETERMINER = r"(?:那|这|一)?(?:件|把|盏|袋|块|张|封|只|枚|本|串|根)?"
_HOLDING = re.compile(
    r"(?:(?:攥|拿|握|抱|捧|拎|提|举|背)着|握住|捡起|拾起|拿起|接过|手里的|腰间的|怀里的)" + _DETERMINER + r"$"
)
_STOWED = re.compile(r"^(?:挂在腰间|攥进手心|收进怀里|揣进怀里|塞进衣袋|背在背上)")


def _closing_details(snapshot, before, prior, previous, dropped):
    """Select closing facts from the saved snapshot, never infer a holder or reveal from a plan."""
    compact = compact_state(snapshot)
    names = snapshot.get("present")
    present = {n for n in names if isinstance(n, str) and n} if isinstance(names, list) else set()
    tail = previous[-1200:]
    held = {}
    for name, obj in compact["objects"].items():
        holder = obj.get("owner")
        if not obj.get("location") or obj["location"] != snapshot.get("end_location") or holder not in present:
            continue
        # Require the recorded holder at the object's last mention. A different named
        # actor handling it later or a mention in dialogue is not a confirmation.
        para = next((p for p in reversed(tail.splitlines()) if name in p), "")
        if not para or any(q in para for q in '“”「」『』"‘’'):
            continue
        sentences = re.split(r"[。！？!?]", para)
        index = max(i for i, s in enumerate(sentences) if name in s)
        mention = sentences[index]
        if holder not in mention or _UNCERTAIN_HOLDER.search(mention) or any(n in mention for n in present - {holder}):
            continue
        leading, _, trailing = mention.rpartition(name)
        if holder not in leading:
            continue
        # A named holder with an observed grip, or the same person picking one
        # from the named group in the next sentence of this paragraph.
        if _HOLDING.search(leading) or (
            re.search(r"(?:把|将)" + _DETERMINER + "$", leading) and _STOWED.match(trailing)
        ):
            held[name] = holder
        elif index + 1 < len(sentences) and re.match(
            re.escape(holder) + r"(?:弯腰|伸手)?(?:捡起|拾起|接过)(?:其中|最大|最小)的一", sentences[index + 1]
        ):
            held[name] = holder
    # A merged snapshot contains years of open threads. Carry newly opened ones
    # here; scheduled callbacks in later chapters already travel with the chapter plan.
    old = compact_state(before)["foreshadows"]
    opened = [
        name
        for name, status in compact["foreshadows"].items()
        if status == "open" and old.get(name) != "open" and (prior == 1 or before is not None)
    ]
    result = {}
    for label, items in (("持物", sorted(held, key=tail.rfind, reverse=True)), ("新增未解线索", opened)):
        kept = [s for s in items if len(s) <= 80 and (label != "持物" or len(held[s]) <= 80)][:4]
        if len(kept) < len(items) and dropped is not None:
            dropped.append(f"上章结尾.{label}:omitted={len(items) - len(kept)}")
        if kept:
            result[label] = {n: held[n] for n in kept} if label == "持物" else kept
    return result


def previous_outcome_facts(state, history, chapter_rows, chapter, previous, dropped=None):
    """Project only the preceding chapter's saved outcome; never send its prose as a summary."""
    prior = chapter - 1
    facts = {}
    snapshot = before = None
    for source, marker in ((history, "chapter"), (chapter_rows, "number")):
        records = source.values() if isinstance(source, dict) else source or []
        for row in records:
            if (
                not isinstance(row, dict)
                or row.get("state_failed")
                or row.get("status", "complete") != "complete"
                or row.get("fact_source") not in (None, "final_body")
            ):
                continue
            if marker == "number" and row.get("fact_source") == "final_body" and isinstance(row.get("state"), dict):
                if row.get(marker) == prior:
                    snapshot = row["state"]
                elif row.get(marker) == prior - 1:
                    before = row["state"]
            if row.get(marker) != prior:
                continue
            summary = row.get("summary")
            if isinstance(summary, str) and summary.strip() and summary not in previous:
                facts["结果"] = summary[:200]
            for field, label in (("gains", "所得"), ("costs", "代价"), ("events", "事件"), ("advances", "进展")):
                items = row.get(field)
                if isinstance(items, list):
                    details = []
                    for item in items:
                        if isinstance(item, dict) and item.get("chapter", prior) != prior:
                            continue
                        value = item.get("content") if isinstance(item, dict) else item
                        if isinstance(value, str) and value.strip() and (len(value) < 16 or value not in previous):
                            details.append(value[:80])
                    if details:
                        facts[label] = details[:4]
    boundary_state = state if snapshot is None else snapshot
    boundary = {k: boundary_state[k] for k in ("end_location", "present", "end_pov") if boundary_state.get(k)}
    if snapshot is not None and previous:
        boundary.update(_closing_details(snapshot, before, prior, previous, dropped))
    return json.dumps(
        {
            "上章已成事实": facts,
            "收场时": boundary,
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
