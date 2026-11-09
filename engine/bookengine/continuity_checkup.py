"""Whole-book diagnostics for repeated character accidents."""

import re

from .precision import _sentences


_PART = re.compile(r"(?:左|右|双)?(?:前腿|后腿|手臂|手掌|手|腿|臂|脚|掌|蹄|角)")


def _cards(bible):
    return {
        card["name"]
        for card in bible.get("characters", [])
        if isinstance(card, dict) and isinstance(card.get("name"), str) and card["name"].strip()
    }


def _cause(sentence):
    for noun, action, label in (
        ("横木", r"压|砸|卡", "横木压住"),
        ("落石", r"压|砸|埋", "落石砸压"),
        ("木梁", r"压|砸|卡", "木梁压住"),
        ("绳", r"缠|勒|绊", "绳索缠勒"),
        ("塌方", r"压|埋|困", "塌方压困"),
    ):
        if noun in sentence and re.search(action, sentence):
            return label
    return ""


def _event_part(sentence):
    for clause in re.split(r"[，,；;]", sentence):
        if _cause(clause) and (part := _PART.search(clause)):
            return part
    return None


def accident_events(bible, chapters):
    result = []
    for chapter in chapters:
        number = chapter.get("number")
        text = chapter.get("text")
        if type(number) is not int or not isinstance(text, str):
            continue
        last_named = None
        for index, (start, end) in enumerate(_sentences(text)):
            sentence = text[start:end]
            mentioned = [name for name in _cards(bible) if name in sentence]
            named = min(mentioned, key=lambda name: (sentence.index(name), -len(name))) if mentioned else None
            if named:
                last_named = (named, index)
            cause = _cause(sentence)
            part = _event_part(sentence)
            if not cause or not part:
                continue
            name = named or (
                last_named[0]
                if last_named and index - last_named[1] <= 1 and re.search(r"[他她它]", sentence)
                else None
            )
            if name:
                result.append(
                    {
                        "name": name,
                        "part": part[0],
                        "cause": cause,
                        "chapter": number,
                        "quote": sentence,
                        "status": "Inference",
                    }
                )
    return result


def repeated_accidents(events, window=10):
    result = []
    seen = {}
    for row in sorted(events, key=lambda item: item["chapter"]):
        key = (row["name"], row["part"], row["cause"])
        previous = seen.get(key)
        if previous and 0 < row["chapter"] - previous["chapter"] <= window:
            result.append(
                {
                    **row,
                    "previous_chapter": previous["chapter"],
                    "previous_quote": previous["quote"],
                    "window": row["chapter"] - previous["chapter"],
                }
            )
        seen[key] = row
    return result


def continuity_checkup(bible, chapters):
    events = accident_events(bible, chapters)
    repeated = repeated_accidents(events)
    return {
        "counts": {"accidents": len(events), "repeated_accidents": len(repeated)},
        "repeated_accidents": repeated,
        "authority": "agent_editorial_not_human",
        "rule": "同一人物、同一部位、同类事故在10章内重复",
    }


def merge_accident_delta(ledger, delta, text, bible, chapter):
    """Merge quote-grounded extracted accidents, retaining a bounded recent window."""
    rows = delta.get("accidents", []) if isinstance(delta, dict) else []
    cards = _cards(bible)
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("name") not in cards:
            continue
        quote = row.get("quote")
        part = row.get("part")
        cause = row.get("cause")
        if (
            not all(isinstance(value, str) and value.strip() for value in (quote, part, cause))
            or quote.strip() not in text
        ):
            continue
        item = {
            "name": row["name"],
            "part": part.strip()[:20],
            "cause": cause.strip()[:30],
            "chapter": chapter,
            "quote": quote.strip(),
        }
        if item not in ledger["accidents"]:
            ledger["accidents"].append(item)
    ledger["accidents"] = [row for row in ledger["accidents"] if row.get("chapter", 0) >= chapter - 10][-30:]
