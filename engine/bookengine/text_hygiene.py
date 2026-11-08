"""Pure publication hygiene checks for generated chapter prose."""

from difflib import SequenceMatcher
from pathlib import Path
import json
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


def summarize_chapters(chapters):
    """Aggregate the three publication hygiene categories for book checkup."""
    rows = [dict(chapter=row["number"], **inspect_text(row["text"])) for row in chapters]
    return dict(
        english_fragments=sum(row["counts"]["english_fragments"] for row in rows),
        structural_notes=sum(row["counts"]["structural_notes"] for row in rows),
        paragraph_repetitions=sum(row["counts"]["paragraph_repetitions"] for row in rows),
        english_chapters=[row["chapter"] for row in rows if row["counts"]["english_fragments"]],
        structural_chapters=[row["chapter"] for row in rows if row["counts"]["structural_notes"]],
        paragraph_repetition_chapters=[row["chapter"] for row in rows if row["counts"]["paragraph_repetitions"]],
        chapters=[
            row
            for row in rows
            if any(row["counts"][key] for key in ("english_fragments", "structural_notes", "paragraph_repetitions"))
        ],
    )


def _repair_targets(text, findings):
    from .precision import _sentences

    paragraph_spans = _paragraph_spans(text)
    duplicate_spans = {(row["start"], row["end"]): row for row in findings["paragraphs"]}
    targets = []
    for (start, end), row in duplicate_spans.items():
        reference = paragraph_spans[row["reference_paragraph"] - 1]
        targets.append(
            dict(
                id="",
                kind=row["kind"],
                start=start,
                end=end,
                text=text[start:end],
                reference_text=text[reference[0] : reference[1]],
            )
        )
    sentence_spans = _sentences(text)
    grouped = {}
    for kind, rows in (("english", findings["english"]), ("structure", findings["structure"])):
        for row in rows:
            span = next(((start, end) for start, end in sentence_spans if start <= row["start"] < end), None)
            if span is None or any(start <= span[0] and span[1] <= end for start, end in duplicate_spans):
                continue
            target = grouped.setdefault(
                span,
                dict(id="", kind=kind, kinds=[], start=span[0], end=span[1], text=text[span[0] : span[1]]),
            )
            target["kinds"].append(kind)
            target["kind"] = "+".join(sorted(set(target["kinds"])))
    targets.extend(grouped.values())
    targets.sort(key=lambda row: row["start"])
    for number, row in enumerate(targets, 1):
        row["id"] = f"H{number}"
    return targets


def _apply_targeted_edits(text, targets, value):
    by_id = {row["id"]: row for row in targets}
    edits = []
    seen = set()
    for proposed in value.get("edits", []) if isinstance(value, dict) else []:
        if not isinstance(proposed, dict) or proposed.get("id") in seen:
            continue
        target = by_id.get(proposed.get("id"))
        replacement = proposed.get("text")
        if target is None or not isinstance(replacement, str) or "\n" in replacement or "\r" in replacement:
            continue
        if not replacement.strip() and "paragraph" not in target["kind"]:
            continue
        seen.add(target["id"])
        if replacement != target["text"]:
            edits.append(dict(target, replacement=replacement))
    candidate = text
    for edit in sorted(edits, key=lambda row: row["start"], reverse=True):
        candidate = candidate[: edit["start"]] + edit["replacement"] + candidate[edit["end"] :]
    candidate = re.sub(r"\n(?:[ \t]*\n){2,}", "\n\n", candidate).strip()
    return candidate, edits


def _last_paragraph(text):
    spans = _paragraph_spans(text)
    return text[spans[-1][0] : spans[-1][1]] if spans else ""


def repair_text(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """Make one targeted repair attempt and adopt only a fully clean recheck."""
    before = inspect_text(text)
    note = dict(status="checked", before=before["counts"], after=before["counts"], edits=[])
    targets = _repair_targets(text, before)
    if not targets:
        return text, raw, failed, note
    patch = ask(
        client,
        prefix + "-text-hygiene",
        "只修改卫生命中的句或后一次重复段：英文换成自然中文，结构说明改成叙述，重复段删除后一次或合并其独有信息。"
        "不得修改其他文字，不增删人物、事实、数字、因果或章末画面；不安全则不提交该项。返回JSON。",
        [("卫生命中", json.dumps(targets, ensure_ascii=False)), ("只读正文", text)],
        '{"edits":[{"id":"H1","text":"替换句；删除重复整段时为空字符串"}]}',
        cap=3000,
        input_limit=10000,
        max_attempts=1,
    )
    candidate, edits = _apply_targeted_edits(text, targets, patch)
    if candidate == text or not edits:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_or_empty_targeted_edits")
    after = inspect_text(candidate)
    if any(after["counts"].values()):
        return (
            text,
            raw,
            failed,
            dict(
                note,
                status="rolled_back",
                reason="hygiene_recheck_failed",
                after=after["counts"],
                attempted_edits=edits,
            ),
        )
    names = [row.get("name") for row in bible.get("characters", []) if isinstance(row, dict) and row.get("name")]
    if _last_paragraph(candidate) != _last_paragraph(text) or any(
        candidate.count(name) < text.count(name) for name in names
    ):
        return text, raw, failed, dict(note, status="rolled_back", reason="character_or_ending_changed")
    checked, invalid, _ = extract(
        client, prefix + "-text-hygiene-recheck", candidate, state, bible, outline, [], final=True
    )
    from .checks import high_confidence

    if invalid or high_confidence(state, checked.get("events", []), candidate):
        return text, raw, failed, dict(note, status="rolled_back", reason="fact_recheck_failed")
    return (
        candidate,
        checked,
        False,
        dict(note, status="repaired", after=after["counts"], edits=edits, repair_call=prefix + "-text-hygiene"),
    )
