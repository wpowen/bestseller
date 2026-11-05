import re
from pathlib import Path
import yaml
from .text import _paragraph_spans, pack, chinese_count

_RULES = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/ending_rules.yaml").read_text())


def ending_candidates(text, bible, outline=None):
    spans = _paragraph_spans(text) if isinstance(text, str) else []
    if not spans:
        return []
    start = spans[-2:][0][0]
    tail, known = text[start:], text[:start] + pack(bible) + pack(outline)
    if not re.search(_RULES["suspense"], tail):
        return []
    return [
        dict(
            kind=kind, text=m[0], start=start + m.start(), end=start + m.end(), known=m["value"] in known, context=tail
        )
        for kind, pattern in _RULES["signals"].items()
        for m in re.finditer(pattern, tail)
    ]


def apply_tail(text, value):
    """Legacy two-paragraph replacement, preserving prefix and separators."""
    spans = _paragraph_spans(text) if isinstance(text, str) else []
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if isinstance(value, str):
        value = [value[a:b] for a, b in _paragraph_spans(value)]
    if len(spans) < 2 or not isinstance(value, (list, tuple)) or len(value) != 2:
        return text, False
    if any(not isinstance(p, str) or not p.strip() or len(_paragraph_spans(p)) != 1 for p in value):
        return text, False
    if re.search(_RULES["rewrite_block"], "\n\n".join(value)):
        return text, False
    result = text
    for (a, b), replacement in reversed(list(zip(spans[-2:], value))):
        result = result[:a] + replacement + result[b:]
    return result, result != text


def scene_context(text):
    """Original 1-based P ids; offset excludes the last scene delimiter."""
    spans = _paragraph_spans(text)
    marks = list(re.finditer(_RULES["scene_boundary"], text))
    floor = marks[-1].end() if marks else len(text) / 2
    rows = [
        {"pid": i, "text": text[max(a, int(floor)) if marks else a : b]}
        for i, (a, b) in enumerate(spans, 1)
        if (b > floor if marks else i >= 2 and a >= floor) and text[max(a, int(floor)) : b].strip()
    ]
    return dict(start=rows[0]["pid"] if rows else None, paragraphs=rows, boundary=bool(marks), offset=int(floor))


def apply_scene(text, value):
    """Replace only a valid final scene; retain all earlier bytes."""
    ctx = scene_context(text)
    start = value.get("start") if isinstance(value, dict) else None
    replacement = value.get("text") if isinstance(value, dict) else None
    if type(start) is not int or start not in [p["pid"] for p in ctx["paragraphs"]]:
        return text, False
    if ctx["boundary"] and start != ctx["start"]:
        return text, False
    if not isinstance(replacement, str) or not replacement.strip():
        return text, False
    parts = [replacement[a:b] for a, b in _paragraph_spans(replacement)]
    if chinese_count(replacement) > _RULES["scene_limit"] or any(
        chinese_count(p) > _RULES["paragraph_limit"] for p in parts
    ):
        return text, False
    if re.search(_RULES["rewrite_block"], replacement) or re.search(_RULES["scene_boundary"], replacement):
        return text, False
    if re.search(_RULES["scene_exposition"], replacement) or not re.search(_RULES["scene_dialogue"], replacement, re.S):
        return text, False
    offset = max(_paragraph_spans(text)[start - 1][0], ctx["offset"] if ctx["boundary"] else 0)
    result = text[:offset] + replacement
    return result, result != text


def english_hits(text):
    """Offsets in the FINAL merged body; position labels alone are allowed."""
    return [
        dict(text=m[0], start=m.start(), end=m.end())
        for m in re.finditer(_RULES["english_fragment"], text)
        if not re.fullmatch(_RULES["position_label"], m[0])
    ]
