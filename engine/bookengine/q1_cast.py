"""Connect conservative cast findings to the existing sentence repair round."""

from .identity_statements import identity_statements
from .introductions import annotate_introductions, check_introductions
from .precision import _sentences

CAST_OBSERVATION_VERSION = 2
# A first mention without an identity is fine: who someone is shows in what they do. The repair
# round fixes stand-alone identity sentences and unregistered aliases, never adds identities.
REPAIR_KINDS = ("identity_statement", "unregistered_alias")


def seen_in(text, cards):
    names = []
    for card in cards:
        aliases = card.get("aliases", [])
        aliases = [aliases] if isinstance(aliases, str) else aliases if isinstance(aliases, list) else []
        labels = [card.get("name"), *aliases]
        if card.get("name") and any(isinstance(label, str) and label and label in text for label in labels):
            names.append(card["name"])
    return names


def previous_cast(out, number, bible, state):
    if state.get("cast_observation_version") == CAST_OBSERVATION_VERSION and "seen_characters" in state:
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
    cards = bible.get("characters", [])
    seen = state.get("seen_characters", [])
    findings = [
        row
        for row in check_introductions(text, cards, seen, names=(outline or {}).get("introductions", []))
        if row["kind"] != "missing_introduction"
    ]
    return findings + [
        dict(
            kind="identity_statement",
            name=row["name"],
            status="Inference",
            message=f"{row['name']}刚出场就单独用一句交代身份；改成他正在做的事或旁人的反应带出，或删去",
            quote=row["quote"],
            start=row["start"],
            end=row["end"],
        )
        for row in identity_statements(text, cards, seen)
    ]


def cast_hits(text, bible, state):
    spans = _sentences(text)
    result = []
    for finding in cast_findings(text, bible, state):
        if finding["kind"] not in REPAIR_KINDS:
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
    return {(f["kind"], f["name"]) for f in findings if f["kind"] in REPAIR_KINDS}
