"""Pure publication hygiene checks for generated chapter prose."""

from difflib import SequenceMatcher
from pathlib import Path
import json
import re
import unicodedata

import yaml

from .text import _paragraph_spans
from .sentence_repetition import sentence_hits
from .model_notes import ENGLISH_WORD_GATE, english_rows as _english_rows
from .model_notes import model_note_hits, repair_model_notes, require_publishable


_RULES = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/ending_rules.yaml").read_text())
_STRUCTURE = re.compile(
    r"第\s*[0-9０-９零〇一二两三四五六七八九十百千]+\s*场戏|"
    r"场景\s*[0-9０-９零〇一二两三四五六七八九十百千]+|本章|章纲|原章"
)
_MIN_EXACT_LENGTH = 10
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
        if exact and len(current["normalized"]) >= _MIN_EXACT_LENGTH:
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
    english = _english_rows(text, spans)
    notes = model_note_hits(text)
    structure = _surface_hits(text, spans, _STRUCTURE, "structure")
    paragraphs = _paragraph_hits(text, spans)
    sentences = sentence_hits(text, spans)
    exact = sum(row["kind"] == "exact_paragraph" for row in paragraphs)
    near = sum(row["kind"] == "near_paragraph" for row in paragraphs)
    return dict(
        counts=dict(
            english_fragments=len(english),
            english_words=len(english),
            english_gate_blocks=int(len(english) > ENGLISH_WORD_GATE),
            model_notes=len(notes),
            structural_notes=len(structure),
            exact_paragraph_repetitions=exact,
            near_paragraph_repetitions=near,
            paragraph_repetitions=exact + near,
            exact_sentence_repetitions=sum(row["kind"] != "near_sentence" for row in sentences),
            near_sentence_repetitions=sum(row["kind"] == "near_sentence" for row in sentences),
            sentence_repetitions=len(sentences),
        ),
        english=english,
        model_notes=notes,
        structure=structure,
        paragraphs=paragraphs,
        sentences=sentences,
    )


def summarize_chapters(chapters):
    """Aggregate the three publication hygiene categories for book checkup."""
    rows = [dict(chapter=row["number"], **inspect_text(row["text"])) for row in chapters]
    return dict(
        english_fragments=sum(row["counts"]["english_fragments"] for row in rows),
        english_gate_blocks=sum(row["counts"]["english_gate_blocks"] for row in rows),
        model_notes=sum(row["counts"]["model_notes"] for row in rows),
        structural_notes=sum(row["counts"]["structural_notes"] for row in rows),
        paragraph_repetitions=sum(row["counts"]["paragraph_repetitions"] for row in rows),
        sentence_repetitions=sum(row["counts"]["sentence_repetitions"] for row in rows),
        english_chapters=[row["chapter"] for row in rows if row["counts"]["english_fragments"]],
        english_gate_chapters=[row["chapter"] for row in rows if row["counts"]["english_gate_blocks"]],
        model_note_chapters=[row["chapter"] for row in rows if row["counts"]["model_notes"]],
        structural_chapters=[row["chapter"] for row in rows if row["counts"]["structural_notes"]],
        paragraph_repetition_chapters=[row["chapter"] for row in rows if row["counts"]["paragraph_repetitions"]],
        sentence_repetition_chapters=[row["chapter"] for row in rows if row["counts"]["sentence_repetitions"]],
        chapters=[
            row
            for row in rows
            if any(
                row["counts"][key]
                for key in ("english_fragments", "structural_notes", "paragraph_repetitions", "sentence_repetitions")
            )
        ],
    )


def _repair_targets(text, findings, bible):
    from .precision import _sentences

    paragraph_spans = _paragraph_spans(text)
    sentence_spans = _sentences(text)
    sentence_rows = [
        row
        for row in findings["sentences"]
        if not any(
            paragraph["kind"] == "exact_paragraph"
            and paragraph["start"] == row["start"]
            and paragraph["end"] == row["end"]
            for paragraph in findings["paragraphs"]
        )
    ]
    duplicate_spans = {
        (row["start"], row["end"]): row
        for row in findings["paragraphs"]
        if not any(row["start"] <= sentence["start"] < row["end"] for sentence in sentence_rows)
    }
    targets = []
    for row in sentence_rows:
        end = row["end"]
        if not row["safe_delete"] and any(reason.endswith("dependency") for reason in row["reasons"]):
            index = max((i for i, span in enumerate(sentence_spans) if span[1] <= row["end"]), default=None)
            if index is not None and index + 1 < len(sentence_spans):
                end = sentence_spans[index + 1][1]
        forced_replacement = None
        if end > row["end"]:
            names = sorted(
                [
                    card.get("name")
                    for card in bible.get("characters", [])
                    if isinstance(card, dict) and card.get("name")
                ],
                key=len,
                reverse=True,
            )
            reference = row["reference_text"].lstrip(" \t\r\n“‘\"'")
            subject = next((name for name in names if reference.startswith(name)), None)
            tail = text[row["end"] : end]
            match = re.match(r"(\s*[“‘\"']?)[他她]", tail)
            if subject and match:
                forced_replacement = match[1] + subject + tail[match.end() :]
        targets.append(
            dict(
                id="",
                kind=row["kind"],
                start=row["start"],
                end=end,
                text=text[row["start"] : end],
                reference_text=row["reference_text"],
                safe_delete=row["safe_delete"],
                reasons=row["reasons"],
                forced_replacement=forced_replacement,
            )
        )
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
    proposed_rows = value.get("edits", []) if isinstance(value, dict) else []
    proposed_rows = [row for row in proposed_rows if isinstance(row, dict)]
    submitted = {row.get("id") for row in proposed_rows}
    proposed_rows += [
        {"id": row["id"], "text": ""}
        for row in targets
        if (row.get("safe_delete") or row.get("forced_replacement") is not None) and row["id"] not in submitted
    ]
    for proposed in proposed_rows:
        if not isinstance(proposed, dict) or proposed.get("id") in seen:
            continue
        target = by_id.get(proposed.get("id"))
        replacement = proposed.get("text")
        if target is None or not isinstance(replacement, str) or "\n" in replacement or "\r" in replacement:
            continue
        if target.get("forced_replacement") is not None:
            replacement = target["forced_replacement"]
        elif target["kind"] == "exact_paragraph" or target.get("safe_delete"):
            replacement = ""
        if not replacement.strip() and "paragraph" not in target["kind"] and not target.get("safe_delete"):
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


def _quote_balance_worse(before, after):
    if any(
        abs(after.count(left) - after.count(right)) > abs(before.count(left) - before.count(right))
        for left, right in (("“", "”"), ("「", "」"), ("『", "』"))
    ):
        return True
    return after.count('"') % 2 > before.count('"') % 2


def repair_text(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """Make one targeted repair attempt and adopt only a fully clean recheck."""
    text, raw, failed, model_notes = repair_model_notes(
        client, prefix, text, raw, failed, state, bible, outline, extract, ask
    )
    before = inspect_text(text)
    note = dict(status="checked", before=before["counts"], after=before["counts"], edits=[], model_notes=model_notes)
    if model_notes["status"] == "unfinalized":
        return text, raw, failed, dict(note, status="unfinalized", reason=model_notes["reason"])
    targets = _repair_targets(text, before, bible)
    if not targets:
        status = "repaired" if model_notes["status"] == "repaired" else "checked"
        return text, raw, failed, dict(note, status=status)
    patch = ask(
        client,
        prefix + "-text-hygiene",
        "只修改卫生命中的句或后一次重复段：英文换成自然中文，结构说明改成叙述，重复段删除后一次或合并其独有信息。"
        "重复整句只删后一次；若后句靠它提供主语，把主语并入后句并保留后句独有信息。"
        "不得修改其他文字，不增删人物、事实、数字、因果或章末画面，引号必须成对；不安全则不提交该项。返回JSON。",
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
    if _quote_balance_worse(text, candidate):
        return text, raw, failed, dict(note, status="rolled_back", reason="punctuation_recheck_failed")
    names = [row.get("name") for row in bible.get("characters", []) if isinstance(row, dict) and row.get("name")]
    if _last_paragraph(candidate) != _last_paragraph(text) or any(
        name in text and name not in candidate for name in names
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
