"""Connect conservative cast findings to the existing sentence repair round."""

from .introductions import annotate_introductions, check_introductions
from .precision import _sentences


def seen_in(text, cards):
    names = []
    for card in cards:
        aliases = card.get("aliases", [])
        aliases = [aliases] if isinstance(aliases, str) else aliases if isinstance(aliases, list) else []
        labels = [card.get("name"), card.get("address"), *aliases]
        if card.get("name") and any(isinstance(label, str) and label and label in text for label in labels):
            names.append(card["name"])
    return names


def previous_cast(out, number, bible, state):
    if "seen_characters" in state:
        return state["seen_characters"]
    cards = bible.get("characters", [])
    return sorted(
        {
            name
            for path in out.glob("chapter-*.body.md")
            if int(path.name.split("-")[1].split(".")[0]) < number
            for name in seen_in(path.read_text(encoding="utf-8"), cards)
        }
    )


def chapter_cast(outline, bible, seen):
    return annotate_introductions([outline], bible.get("characters", []), seen)[0]


def cast_findings(text, bible, state, outline=None):
    return check_introductions(
        text,
        bible.get("characters", []),
        state.get("seen_characters", []),
        names=(outline or {}).get("introductions", []),
    )


def cast_hits(text, bible, state):
    spans = _sentences(text)
    result = []
    for finding in cast_findings(text, bible, state):
        if finding["kind"] not in ("missing_introduction", "unregistered_alias"):
            continue
        for number, (start, end) in enumerate(spans, 1):
            if start <= finding["start"] < end:
                result.append(
                    dict(
                        id=number,
                        start=start,
                        end=end,
                        kind=finding["kind"],
                        text=text[start:end],
                        reason=finding["message"],
                        name=finding["name"],
                    )
                )
                break
    return result


def remaining_cast_problems(findings):
    return {(f["kind"], f["name"]) for f in findings if f["kind"] in ("missing_introduction", "unregistered_alias")}
