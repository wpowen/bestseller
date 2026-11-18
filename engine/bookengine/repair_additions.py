"""Instruction 68 (E3): paragraphs that repairs add may not restate what the chapter already says.

A repair paragraph is one that is in the repaired text but not, verbatim, in the writer's draft.
It is compared with every other paragraph of the chapter, not only its neighbours.
"""

import re
from difflib import SequenceMatcher

from .text import _paragraph_spans

RULES = dict(
    ngram=3,
    # Calibrated on five saved 50-chapter books (reports/life-names-repairs-68.md).
    containment_min=0.45,
    min_characters=40,
    copied_line_min=6,
    copied_lines=2,
    long_copied_line=12,
)
_QUOTED = re.compile(r"[“「]([^”」]+)[”」]")
_HAN = re.compile(r"[㐀-鿿]")


def _paragraphs(text: str) -> list[str]:
    return [text[start:end] for start, end in _paragraph_spans(text)]


def _han(text: str) -> str:
    return "".join(_HAN.findall(text))


def _grams(text: str) -> set[str]:
    plain, size = _han(text), RULES["ngram"]
    return {plain[index : index + size] for index in range(len(plain) - size + 1)}


def _lines(paragraph: str) -> list[str]:
    return [line for line in (_han(m) for m in _QUOTED.findall(paragraph)) if len(line) >= RULES["copied_line_min"]]


def _finding(index: int, paragraph: str, others: list[tuple[int, str]]) -> dict | None:
    own_lines = _lines(paragraph)
    for other_index, other in others:
        other_lines = set(_lines(other))
        copied = [line for line in own_lines if line in other_lines]
        if len(copied) >= RULES["copied_lines"] or any(len(line) >= RULES["long_copied_line"] for line in copied):
            return dict(kind="copied_dialogue", paragraph=index + 1, source=other_index + 1, lines=copied)
    grams = _grams(paragraph)
    if len(_han(paragraph)) < RULES["min_characters"] or not grams:
        return None
    best = max(((len(grams & _grams(other)) / len(grams), other_index) for other_index, other in others), default=None)
    if best and best[0] >= RULES["containment_min"]:
        return dict(kind="restated_paragraph", paragraph=index + 1, source=best[1] + 1, containment=round(best[0], 3))
    return None


def added_paragraph_findings(original: str, candidate: str) -> list[dict]:
    """Repair paragraphs that copy existing dialogue or restate another paragraph of the chapter."""
    drafted = {paragraph.strip() for paragraph in _paragraphs(original)}
    paragraphs = _paragraphs(candidate)
    findings = []
    for index, paragraph in enumerate(paragraphs):
        if paragraph.strip() in drafted:
            continue
        others = [(other, text) for other, text in enumerate(paragraphs) if other != index]
        found = _finding(index, paragraph, others)
        if found:
            findings.append(dict(found, quote=paragraph[:120]))
    return findings


def guard_repair_additions(original: str, candidate: str) -> tuple[str, dict]:
    """Drop duplicate insertions; restore contiguous rewritten blocks containing a duplicate.

    Adjacent replacements can depend on each other: restoring only the copied-dialogue
    paragraph can leave its repeated action in the next rewritten paragraph.
    """
    findings = added_paragraph_findings(original, candidate)
    if not findings:
        return candidate, {"status": "checked", "findings": []}
    old, new = _paragraphs(original), _paragraphs(candidate)
    mapping = {}
    flagged = {row["paragraph"] - 1 for row in findings}
    for tag, old_start, old_end, new_start, new_end in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "replace" and old_end - old_start == new_end - new_start:
            mapping.update({new_start + offset: old_start + offset for offset in range(new_end - new_start)})
            block = set(range(new_start, new_end))
            if block & flagged:
                flagged.update(block)
    kept = [old[mapping[index]] if index in flagged and index in mapping else paragraph
            for index, paragraph in enumerate(new) if index not in flagged or index in mapping]  # fmt: skip
    restored = "\n\n".join(part.strip() for part in kept)
    if added_paragraph_findings(original, restored):
        return original, {"status": "rolled_back", "reason": "repair_restates_chapter", "findings": findings}
    return restored, {"status": "partially_rolled_back", "reason": "repair_restates_chapter", "findings": findings}
