"""Source-bound character diagnostics and verification for model-assisted baselines."""

from collections import Counter, defaultdict
import re


FIELDS = ("flaw", "bottom_line", "voice", "catchphrase")
ROLES = {"antagonist", "opponent", "villain", "反派", "对手"}


def _rows(value):
    if isinstance(value, dict):
        return [dict(item, name=name) if isinstance(item, dict) else {"name": name} for name, item in value.items()]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _labels(card, include_address=False):
    aliases = card.get("aliases", [])
    aliases = [aliases] if isinstance(aliases, str) else aliases if isinstance(aliases, list) else []
    values = (card.get("name"), card.get("address"), *aliases) if include_address else (card.get("name"), *aliases)
    return [value for value in values if isinstance(value, str) and value]


def _dialogues(bible, chapters):
    cards = _rows(bible.get("characters"))
    owners = {label: card.get("name") for card in cards for label in _labels(card)}
    if not owners:
        return []
    names = "|".join(sorted((re.escape(name) for name in owners), key=len, reverse=True))
    verb = r"(?:说|道|问|答|喊|叫|喝|骂|劝|提醒|低声道|沉声道|开口)"
    patterns = (
        re.compile(rf"(?P<label>{names})(?:[^。！？\n“”「」]{{0,12}})?{verb}[：:]?[“「](?P<quote>[^”」\n]+)[”」]"),
        re.compile(rf"[“「](?P<quote>[^”」\n]+)[”」][，,。]?(?P<label>{names}){verb}"),
    )
    found = []
    for chapter in chapters:
        matches = []
        for pattern in patterns:
            matches.extend((match.start(), match) for match in pattern.finditer(chapter["text"]))
        seen = set()
        for _, match in sorted(matches, key=lambda item: item[0]):
            key = (match.start(), match.group("quote"))
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "chapter": chapter["number"],
                    "speaker": owners[match.group("label")],
                    "quote": match.group("quote").strip(),
                }
            )
    return found


def dialogue_samples(bible, chapters, minimum=20):
    """Select a balanced, explicitly attributed pool and remove all cast labels."""
    cards = _rows(bible.get("characters"))
    labels = sorted({label for card in cards for label in _labels(card, include_address=True)}, key=len, reverse=True)
    grouped = defaultdict(list)
    for row in _dialogues(bible, chapters):
        masked = row["quote"]
        for label in labels:
            masked = masked.replace(label, "某人")
        grouped[row["speaker"]].append(dict(row, masked=masked))
    order = [card.get("name") for card in cards if grouped.get(card.get("name"))]
    selected = []
    for index in range(max((len(grouped[name]) for name in order), default=0)):
        for name in order:
            if index < len(grouped[name]):
                selected.append(grouped[name][index])
                if len(selected) == minimum:
                    break
        if len(selected) == minimum:
            break
    for index, row in enumerate(selected, 1):
        row["id"] = index
    return selected


def score_guesses(samples, guesses):
    guessed = {row.get("id"): row.get("speaker") for row in guesses if isinstance(row, dict)}
    per = defaultdict(lambda: {"total": 0, "correct": 0})
    confusion = Counter()
    correct = 0
    for sample in samples:
        actual = sample["speaker"]
        answer = guessed.get(sample["id"], "Unknown")
        per[actual]["total"] += 1
        if answer == actual:
            correct += 1
            per[actual]["correct"] += 1
        else:
            confusion[(actual, answer)] += 1
    for row in per.values():
        row["accuracy"] = round(row["correct"] / row["total"], 4)
    return {
        "total": len(samples),
        "correct": correct,
        "accuracy": round(correct / len(samples), 4) if samples else None,
        "per_character": dict(per),
        "confusion": [
            {"actual": actual, "guessed": answer, "count": count}
            for (actual, answer), count in sorted(confusion.items())
        ],
    }


def verify_fulfillment(raw, bible, chapters):
    """Adopt only model quotes found verbatim in the claimed saved chapter."""
    texts = {chapter["number"]: chapter["text"] for chapter in chapters}
    cards = {card.get("name"): card for card in _rows(bible.get("characters")) if card.get("name")}
    result = {
        name: {field: {"status": "Unknown", "reason": "no_verified_quote"} for field in FIELDS if card.get(field)}
        for name, card in cards.items()
    }
    findings = raw.get("findings", []) if isinstance(raw, dict) else []
    for finding in findings if isinstance(findings, list) else []:
        if not isinstance(finding, dict):
            continue
        name, field = finding.get("character"), finding.get("field")
        chapter, quote = finding.get("chapter"), finding.get("quote")
        if (
            name in result
            and field in result[name]
            and isinstance(quote, str)
            and quote
            and quote in texts.get(chapter, "")
        ):
            result[name][field] = {"status": "Evidence", "chapter": chapter, "quote": quote}
    return result


def character_reference(bible, chapters):
    dialogues = _dialogues(bible, chapters)
    cards = _rows(bible.get("characters"))
    opponents = {card["name"] for card in cards if card.get("name") and card.get("role") in ROLES}
    opponents.update(card["name"] for card in _rows(bible.get("antagonists")) if card.get("name"))
    samples = dialogue_samples(bible, chapters)
    return {
        "reference_only": True,
        "blind_sample_pool": {
            "sample_count": len(samples),
            "speaker_count": len({sample["speaker"] for sample in samples}),
            "minimum_samples": 20,
            "minimum_speakers": 4,
            "status": "Evidence" if len(samples) >= 20 and len({s["speaker"] for s in samples}) >= 4 else "Unknown",
        },
        "fulfillment": verify_fulfillment({"findings": []}, bible, chapters),
        "antagonist_presence": [
            {
                "name": name,
                "chapters": [chapter["number"] for chapter in chapters if name in chapter["text"]],
                "mention_count": sum(chapter["text"].count(name) for chapter in chapters),
                "dialogue_count": sum(row["speaker"] == name for row in dialogues),
                "status": "Evidence",
            }
            for name in sorted(opponents)
        ],
    }
