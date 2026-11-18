"""Stand-alone identity sentences right where a character first appears.

The chapter loop used to require a card-matching identity at every first mention, so the
writer opened chapters like a cast list: “她是夜市里修旧物的摊主”“他是个四处跑腿的青年”
“他经营夜市赊账摊多年”, one per newcomer (《摆摊真的能转运吗》 chapter 1). Identity should
come out of what the person does, carries or is called by others. This module only finds
the sentences; it never decides that a missing identity is a defect.
"""

import re

PARAGRAPHS = 3
_LEAD = r"(?:原本|本来|原来|平日|平时|向来|一直|早年|多年来)?"
_VERB = (
    r"(?:是个|是一名|是一位|是一个|本是|原是|曾是|乃是|是)[^，。！？\n]{0,16}的[一-鿿]{1,6}(?=[，。；])"
    r"|(?:经营|干了|做了|当了|开了|摆了)[^，。！？\n]{1,12}(?:多年|半辈子|十几年|几十年)"
    r"|(?:平日|平时|向来)[^，。！？\n]{2,16}"
)
_SENTENCE = re.compile(r"[^。！？\n]+[。！？]?")
_DIALOGUE = re.compile(r"“[^”\n]*”?")


def _names(cards):
    return [
        card["name"]
        for card in cards or []
        if isinstance(card, dict) and isinstance(card.get("name"), str) and len(card["name"]) >= 2
    ]


def _statement(sentence, name):
    subject = r"(?:他|她|" + re.escape(name) + ")"
    return re.match(subject + _LEAD + "(?:" + _VERB + ")", sentence.lstrip(" \u3000”")) is not None


def _narration(text):
    """Dialogue blanked to spaces so offsets stay valid and nobody's speech counts as narration."""
    return _DIALOGUE.sub(lambda m: " " * len(m.group()), text)


def identity_statements(text, cards, previously_seen=()):
    """[{name, quote, start, end}]: an identity sentence in the first PARAGRAPHS paragraphs naming someone new."""
    seen = set(previously_seen or ())
    plain = _narration(text)
    paragraphs = [(m.start(), m.group()) for m in re.finditer(r"[^\n]+", plain)]
    found = []
    for name in _names(cards):
        if name in seen:
            continue
        for offset, paragraph in [row for row in paragraphs if name in text[row[0] : row[0] + len(row[1])]][
            :PARAGRAPHS
        ]:
            hit = next(
                (m for m in _SENTENCE.finditer(paragraph) if m.group().strip() and _statement(m.group(), name)), None
            )
            start = offset + hit.start() if hit else None
            if hit and all(row["start"] != start for row in found):
                found.append(
                    dict(name=name, quote=text[start : offset + hit.end()].strip(), start=start, end=offset + hit.end())
                )
                break
    return sorted(found, key=lambda row: row["start"])


def book_identity_statements(cards, chapters):
    """Checkup view over chapters in order: each card is judged only in the chapter it first appears."""
    seen, rows = set(), []
    for chapter in sorted(chapters, key=lambda row: row["number"]):
        for row in identity_statements(chapter["text"], cards, seen):
            rows.append(dict(chapter=chapter["number"], name=row["name"], quote=row["quote"]))
        seen.update(name for name in _names(cards) if name in chapter["text"])
    stacked = sorted({row["chapter"] for row in rows if sum(r["chapter"] == row["chapter"] for r in rows) >= 2})
    return dict(
        count=len(rows),
        stacked_chapters=stacked,
        issues=rows,
        status="Inference",
        basis="sentence right after a first mention states who the person is; reader judgment still required",
    )
