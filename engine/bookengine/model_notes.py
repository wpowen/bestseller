"""Deterministic model-note detection and publication blocking."""

import json
from pathlib import Path
import re

import yaml

from .text import _paragraph_spans


_RULES = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/ending_rules.yaml").read_text())
_ENGLISH = re.compile(_RULES["english_fragment"])
_POSITION_LABEL = re.compile(_RULES["position_label"])
_NOTE_RULES = _RULES["model_notes"]
_NOTE_CUE = re.compile(_NOTE_RULES["cue_pattern"])
_NOTE_MARKER = re.compile(_NOTE_RULES["marker_pattern"], re.I)
ENGLISH_WORD_GATE = int(_NOTE_RULES["english_word_gate"])


def english_rows(text, spans=None):
    spans = spans if spans is not None else _paragraph_spans(text)
    return [
        {"text": match[0], "start": match.start(), "end": match.end(), "paragraph": paragraph}
        for match in _ENGLISH.finditer(text)
        if not _POSITION_LABEL.fullmatch(match[0])
        for paragraph, (start, end) in enumerate(spans, 1)
        if start <= match.start() < end
    ]


def model_note_hits(text):
    """Locate model planning prose, preserving Chinese prose after a closing think marker."""
    if not isinstance(text, str):
        return []
    rows = []
    for paragraph, (start, end) in enumerate(_paragraph_spans(text), 1):
        passage = text[start:end]
        closing = re.search(r"</mm:think>", passage, re.I)
        candidate_end = start + closing.end() if closing else end
        candidate = text[start:candidate_end]
        words = list(_ENGLISH.finditer(candidate))
        words = [match for match in words if not _POSITION_LABEL.fullmatch(match[0])]
        latin = sum(char.isascii() and char.isalpha() for char in candidate)
        visible = sum(not char.isspace() for char in candidate)
        cues = list(_NOTE_CUE.finditer(candidate))
        note = bool(_NOTE_MARKER.search(candidate)) or (
            len(words) >= int(_NOTE_RULES["paragraph_min_english_words"])
            and bool(cues)
            and latin / max(1, visible) >= float(_NOTE_RULES["paragraph_english_ratio"])
        )
        if note:
            rows.append(
                dict(
                    kind="model_note",
                    paragraph=paragraph,
                    start=start,
                    end=candidate_end,
                    text=candidate,
                    english_words=len(words),
                    cue_count=len(cues),
                )
            )
    grouped = []
    for row in rows:
        if grouped and not text[grouped[-1]["end"] : row["start"]].strip():
            grouped[-1].update(
                end=row["end"],
                text=text[grouped[-1]["start"] : row["end"]],
                english_words=grouped[-1]["english_words"] + row["english_words"],
                cue_count=grouped[-1]["cue_count"] + row["cue_count"],
            )
        else:
            grouped.append(dict(row))
    return grouped


def publication_blocks(text):
    notes = model_note_hits(text)
    english = english_rows(text)
    blocks = []
    if notes:
        blocks.append(dict(kind="model_note", count=len(notes), samples=[row["text"][:120] for row in notes]))
    if len(english) > ENGLISH_WORD_GATE:
        blocks.append(dict(kind="english_word_gate", count=len(english), threshold=ENGLISH_WORD_GATE))
    return blocks


def require_publishable(text, chapter, out):
    """Persist an unfinalized checkpoint and stop before a blocked body can be saved."""
    blocks = publication_blocks(text)
    if not blocks:
        return text
    folder = Path(out) / "chapters"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = folder / f"{chapter:03d}.unfinalized.json"
    marker.write_text(
        json.dumps({"chapter": chapter, "status": "unfinalized", "blocks": blocks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    kinds = ", ".join(block["kind"].replace("_", " ") for block in blocks)
    raise RuntimeError(f"chapter {chapter} unfinalized: {kinds}")


def repair_model_notes(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """Delete one contiguous note block, bridge its gap once, then recheck facts."""
    hits = model_note_hits(text)
    if not hits:
        return text, raw, failed, {"status": "checked", "count": 0}
    if len(hits) != 1:
        return text, raw, failed, {"status": "unfinalized", "reason": "multiple_note_blocks", "count": len(hits)}
    hit = hits[0]
    before, after = text[: hit["start"]].rstrip(), text[hit["end"] :].lstrip()
    value = ask(
        client,
        prefix + "-model-note-gap",
        "英文写作笔记已直接删除。只判断删除处是否需要一个过渡段；需要时补足动作衔接，不需要则text为空。"
        "不得翻译或复述笔记，不改两侧原文，不增人物、物件、伤病、谜团或任务。返回JSON。",
        [("删除前", before[-1200:]), ("删除后", after[:1200])],
        '{"text":"一个必要的中文过渡段，或空字符串"}',
        cap=900,
        input_limit=3000,
        max_attempts=1,
    )
    bridge = value.get("text") if isinstance(value, dict) else None
    if not isinstance(bridge, str) or len(bridge) > 900 or model_note_hits(bridge):
        return text, raw, failed, {"status": "unfinalized", "reason": "invalid_gap_patch", "count": 1}
    parts = [part for part in (before, bridge.strip(), after) if part]
    candidate = "\n\n".join(parts)
    if publication_blocks(candidate):
        return text, raw, failed, {"status": "unfinalized", "reason": "publication_recheck_failed", "count": 1}
    checked, invalid, _ = extract(
        client, prefix + "-model-note-recheck", candidate, state, bible, outline, [], final=True
    )
    from .checks import high_confidence

    if invalid or high_confidence(state, checked.get("events", []), candidate):
        rewrite = ask(
            client,
            prefix + "-model-note-scene-rewrite",
            "删除笔记后的局部事实复检失败。只重写给出的断开处，保留其中人物、事实、因果和结果，不增谜团。返回JSON。",
            [("断开处前", before[-1200:]), ("断开处后", after[:1200])],
            '{"text":"连贯的局部场景"}',
            cap=1800,
            input_limit=3000,
            max_attempts=1,
        )
        scene = rewrite.get("text") if isinstance(rewrite, dict) else None
        if not isinstance(scene, str) or not scene.strip() or len(scene) > 1800 or publication_blocks(scene):
            return text, raw, failed, {"status": "unfinalized", "reason": "invalid_scene_rewrite", "count": 1}
        candidate = "\n\n".join(part for part in (before, scene.strip(), after) if part)
        checked, invalid, _ = extract(
            client, prefix + "-model-note-scene-recheck", candidate, state, bible, outline, [], final=True
        )
        if invalid or publication_blocks(candidate) or high_confidence(state, checked.get("events", []), candidate):
            return text, raw, failed, {"status": "unfinalized", "reason": "scene_recheck_failed", "count": 1}
    return (
        candidate,
        checked,
        False,
        {
            "status": "repaired",
            "count": 1,
            "deleted_english_words": hit["english_words"],
            "bridge_added": bool(bridge.strip()),
        },
    )
