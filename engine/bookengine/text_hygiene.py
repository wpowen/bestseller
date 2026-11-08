"""Pure publication hygiene checks for generated chapter prose."""

from difflib import SequenceMatcher
from pathlib import Path
import re
import unicodedata

import yaml

from .text import _paragraph_spans


_RULES = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/ending_rules.yaml").read_text())
_ENGLISH = re.compile(_RULES["english_fragment"])
_POSITION_LABEL = re.compile(_RULES["position_label"])
_STRUCTURE = re.compile(
    r"第\s*[0-9０-９零〇一二两三四五六七八九十百千]+\s*场戏|"
    r"场景\s*[0-9０-９零〇一二两三四五六七八九十百千]+|本章|章纲|原章"
)
_MIN_NEAR_LENGTH = 20
_NEAR_SIMILARITY = 0.65
_NEAR_CONTAINMENT = 0.55


def _normalize(value):
    value = unicodedata.normalize("NFKC", value)
    return "".join(char for char in value if not char.isspace() and unicodedata.category(char)[0] not in "PZC")


def _grams(value, size=3):
    return {value[i : i + size] for i in range(max(0, len(value) - size + 1))}


def _paragraph_number(spans, offset):
    return next((number for number, (start, end) in enumerate(spans, 1) if start <= offset < end), None)


def _surface_hits(text, spans, pattern, kind):
    return [
        dict(
            kind=kind,
            text=match[0],
            start=match.start(),
            end=match.end(),
            paragraph=_paragraph_number(spans, match.start()),
        )
        for match in pattern.finditer(text)
    ]


def _paragraph_hits(text, spans):
    rows = [
        dict(
            paragraph=number,
            start=start,
            end=end,
            text=text[start:end],
            normalized=_normalize(text[start:end]),
        )
        for number, (start, end) in enumerate(spans, 1)
    ]
    result = []
    for index, current in enumerate(rows):
        exact = next(
            (row for row in rows[:index] if row["normalized"] and row["normalized"] == current["normalized"]),
            None,
        )
        if exact:
            result.append(
                dict(
                    kind="exact_paragraph",
                    reference_paragraph=exact["paragraph"],
                    paragraph=current["paragraph"],
                    start=current["start"],
                    end=current["end"],
                    text=current["text"],
                    similarity=1.0,
                    containment=1.0,
                )
            )
            continue
        if len(current["normalized"]) < _MIN_NEAR_LENGTH:
            continue
        right = _grams(current["normalized"])
        candidates = []
        for earlier in rows[:index]:
            if len(earlier["normalized"]) < _MIN_NEAR_LENGTH:
                continue
            left = _grams(earlier["normalized"])
            overlap = len(left & right)
            containment = overlap / max(1, min(len(left), len(right)))
            similarity = SequenceMatcher(None, earlier["normalized"], current["normalized"], autojunk=False).ratio()
            if similarity >= _NEAR_SIMILARITY and containment >= _NEAR_CONTAINMENT:
                candidates.append((similarity, containment, earlier))
        if candidates:
            similarity, containment, earlier = max(candidates, key=lambda item: (item[0], item[1]))
            result.append(
                dict(
                    kind="near_paragraph",
                    reference_paragraph=earlier["paragraph"],
                    paragraph=current["paragraph"],
                    start=current["start"],
                    end=current["end"],
                    text=current["text"],
                    similarity=round(similarity, 4),
                    containment=round(containment, 4),
                )
            )
    return result


def inspect_text(text):
    """Return located hygiene findings without changing the supplied text."""
    if not isinstance(text, str):
        text = ""
    spans = _paragraph_spans(text)
    english = [
        row for row in _surface_hits(text, spans, _ENGLISH, "english") if not _POSITION_LABEL.fullmatch(row["text"])
    ]
    structure = _surface_hits(text, spans, _STRUCTURE, "structure")
    paragraphs = _paragraph_hits(text, spans)
    exact = sum(row["kind"] == "exact_paragraph" for row in paragraphs)
    near = sum(row["kind"] == "near_paragraph" for row in paragraphs)
    return dict(
        counts=dict(
            english_fragments=len(english),
            structural_notes=len(structure),
            exact_paragraph_repetitions=exact,
            near_paragraph_repetitions=near,
            paragraph_repetitions=exact + near,
        ),
        english=english,
        structure=structure,
        paragraphs=paragraphs,
    )
