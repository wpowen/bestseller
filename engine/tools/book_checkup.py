"""Independent prose cleaning and deterministic, reference-only book diagnostics."""

import re


_CHAPTER_LINE = re.compile(r"^第[0-9０-９零〇一二三四五六七八九十百千万两]+[章场幕](?:\s|[：:]|$).*$")
_SEPARATOR = re.compile(r"(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,}")
_EMOJI_UNIT = r"(?:[\U0001F1E6-\U0001F1FF]{2}|[0-9#*]\ufe0f?\u20e3|[©®™]\ufe0f|[\U0001F000-\U0001FAFF\u2600-\u27bf])\ufe0f?[\U0001F3FB-\U0001F3FF]?"
_EMOJI = re.compile(_EMOJI_UNIT + r"(?:\u200d" + _EMOJI_UNIT + r")*|[\ufe0f\u200d]")
_PRIVATE = re.compile(r"[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")


def clean_prose(text):
    """Return (text, counts); remove only standalone structure and unwanted glyphs.

    Counts are removed lines for structure, sequences for emoji, and codepoints
    for private use. Normal paragraphs, punctuation and line endings are retained.
    No filesystem or model calls; safe for both writing and polishing clients.
    """
    counts = dict(markdown_headings=0, chapter_headings=0, separators=0, emoji=0, private_use=0)
    kept = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        kind = None
        if re.match(r"^#{1,6}(?:\s|$)", stripped):
            kind = "markdown_headings"
        elif _CHAPTER_LINE.fullmatch(stripped) and not re.search(r"[。！？!?]", stripped):
            kind = "chapter_headings"
        elif _SEPARATOR.fullmatch(stripped):
            kind = "separators"
        if kind:
            counts[kind] += 1
        else:
            kept.append(line)
    cleaned, counts["emoji"] = _EMOJI.subn("", "".join(kept))
    cleaned, counts["private_use"] = _PRIVATE.subn("", cleaned)
    counts["total"] = sum(counts.values())
    return cleaned, counts
