"""Small edit-application helpers for cast consistency repair."""

from .precision import apply as sentence_edits


def apply_cast_edits(text, proposed, targets):
    direct_kinds = {"nonhuman_speech", "nonhuman_speech_origin"}
    direct_targets = {row["id"]: row for row in targets if row["kind"] in direct_kinds}
    direct_proposed = {
        row.get("id"): row.get("text")
        for row in proposed
        if isinstance(row, dict) and row.get("id") in direct_targets and isinstance(row.get("text"), str)
    }
    remaining_targets = [row for row in targets if row["id"] not in direct_targets]
    remaining_proposed = [row for row in proposed if row.get("id") not in direct_targets]
    candidate, edits = sentence_edits(
        text,
        remaining_proposed,
        remaining_targets,
        allow_empty=False,
        allow_style=True,
    )
    for number, replacement in direct_proposed.items():
        target = direct_targets[number]
        if not replacement.strip() or "\n" in replacement or "\r" in replacement or target["quote"] not in candidate:
            continue
        candidate = candidate.replace(target["quote"], replacement, 1)
        edits.append(dict(id=number, text=replacement))
    return candidate, edits


def registered_labels(cards):
    labels = []
    for card in cards:
        aliases = card.get("aliases", [])
        labels.extend([aliases] if isinstance(aliases, str) else aliases if isinstance(aliases, list) else [])
        labels.extend([card.get("name"), card.get("address")])
    return {label for label in labels if isinstance(label, str) and label}
