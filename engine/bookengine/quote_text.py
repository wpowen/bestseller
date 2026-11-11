"""Locate continuous literal text while allowing formatting whitespace differences."""

from .text import _paragraph_spans


_PAIRS = (("“", "”"), ("「", "」"), ("『", "』"))


def literal_quote(text, supplied):
    """Return the exact source slice; never skip words, change punctuation or rewrite input."""
    if not isinstance(supplied, str) or not supplied.strip():
        return None
    if supplied in text:
        return supplied
    positions = [i for i, char in enumerate(text) if not char.isspace()]
    compact = "".join(text[i] for i in positions)
    needle = "".join(char for char in supplied if not char.isspace())
    start = compact.find(needle)
    if start < 0 or not needle:
        return None
    return text[positions[start] : positions[start + len(needle) - 1] + 1]


def unclosed_quote_hits(text):
    """Report per-paragraph Chinese quote imbalance without guessing across paragraphs."""
    if not isinstance(text, str):
        return []
    result = []
    for paragraph, (start, end) in enumerate(_paragraph_spans(text), 1):
        passage = text[start:end]
        for left, right in _PAIRS:
            balance = passage.count(left) - passage.count(right)
            if balance:
                result.append(
                    {
                        "kind": "unclosed_quote",
                        "paragraph": paragraph,
                        "start": start,
                        "end": end,
                        "text": passage,
                        "missing": right if balance > 0 else left,
                        "difference": abs(balance),
                    }
                )
    return result


def _quote_stripped(text):
    return text.translate(str.maketrans("", "", "“”「」『』"))


def repair_unclosed_quotes(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """Insert one uniquely determined delimiter per hit and preserve every dialogue byte."""
    hits = unclosed_quote_hits(text)
    note = {"status": "checked", "before": len(hits), "after": len(hits), "issues": hits, "edits": []}
    if not hits:
        return text, raw, failed, note
    if any(row["difference"] != 1 for row in hits) or len({row["paragraph"] for row in hits}) != len(hits):
        return text, raw, failed, dict(note, status="rolled_back", reason="ambiguous_quote_balance")
    candidate = text
    edits = []
    for row in sorted(hits, key=lambda item: item["start"], reverse=True):
        if row["missing"] in "”」』":
            offset = row["end"]
        else:
            right = {"“": "”", "「": "」", "『": "』"}[row["missing"]]
            close = candidate.find(right, row["start"], row["end"])
            colon = max(candidate.rfind("：", row["start"], close), candidate.rfind(":", row["start"], close))
            offset = colon + 1 if colon >= row["start"] else row["start"]
        candidate = candidate[:offset] + row["missing"] + candidate[offset:]
        edits.append({"paragraph": row["paragraph"], "offset": offset, "text": row["missing"]})
    remaining = unclosed_quote_hits(candidate)
    if remaining or _quote_stripped(candidate) != _quote_stripped(text):
        return text, raw, failed, dict(note, status="rolled_back", reason="quote_recheck_failed", remaining=remaining)
    checked, invalid, _ = extract(client, prefix + "-quote-recheck", candidate, state, bible, outline, [], final=True)
    from .checks import high_confidence

    if invalid or high_confidence(state, checked.get("events", []), candidate):
        return text, raw, failed, dict(note, status="rolled_back", reason="fact_recheck_failed")
    return candidate, checked, False, dict(note, status="repaired", after=0, edits=list(reversed(edits)))


def quote_checkup(chapters):
    rows = [{"chapter": chapter["number"], "issues": unclosed_quote_hits(chapter["text"])} for chapter in chapters]
    return {
        "count": sum(len(row["issues"]) for row in rows),
        "chapters": [row["chapter"] for row in rows if row["issues"]],
        "issues": [dict(chapter=row["chapter"], **issue) for row in rows for issue in row["issues"]],
        "status": "Evidence",
    }
