"""Apply the finish review to the draft, retaining unrelated text and its evidence."""

from itertools import count
from os.path import commonprefix

from .endings import apply_scene
from .precision import apply as sentence_edits
from .q1_extract import body_of, clean, prose_hits
from .repair_placement import place_guarded_patch
from .storage import save
from .text import _sentences, chinese_count


def length_edit_status(original, candidate, word_range):
    if not candidate:
        return "not_proposed"
    low, high = word_range
    original_chars, candidate_chars = chinese_count(original), chinese_count(candidate)
    if low <= original_chars <= high:
        return "original_in_band"
    if not low <= candidate_chars <= high:
        return "candidate_out_of_band"

    # A matching title or closing remark cannot authorize replacing the rest of a chapter.
    # At least half of each version must survive verbatim around the changed passage.
    prefix = commonprefix((original, candidate))
    suffix = commonprefix((original[len(prefix) :][::-1], candidate[len(prefix) :][::-1]))
    retained_chars = chinese_count(prefix) + chinese_count(suffix)
    return "accepted" if 2 * retained_chars >= max(original_chars, candidate_chars) else "nonlocal_rewrite"


def retained_state_stage(out, prefix, text):
    """Reuse a state extraction only for the exact body it read; the chapter runner holds the book lock."""
    for version in count(1):
        stage = f"{prefix}-retained-state-{version}"
        source = out / "steps" / (stage + ".input.md")
        if source.exists():
            if source.read_text(encoding="utf-8") == text:
                return stage
        elif not any((out / "steps" / (stage + suffix)).exists() for suffix in (".json", ".recovered.json")):
            save(source, text)
            return stage


def supplement(text, value):
    changed, placement = place_guarded_patch(text, value, max_chars=400)
    return (clean(changed), True) if placement["status"] == "placed" else (text, False)


def amend(text, raw, bible, state, outline):
    hits = prose_hits(text, bible, state)
    detail = dict(edits=[], hits_before=len(hits), adoption_failed=False, remaining=[], patch=False, body_adopted=False)
    if outline.get("ending") and raw.get("scene"):
        changed, accepted = apply_scene(text, raw["scene"])
        return changed, dict(detail, patch=accepted)
    body = body_of(raw.get("body", ""), None, detail.setdefault("body_cleaning", {}))
    detail["length_revision"] = length_edit_status(text, body, outline.get("word_range", (1870, 2700)))
    spans = _sentences(text)
    changed, edits = sentence_edits(
        text,
        raw.get("edits", []),
        [dict(id=i, start=a, end=b) for i, (a, b) in enumerate(spans, 1) if i in {h["id"] for h in hits}],
        allow_empty=True,
        allow_style=True,
    )
    bad = (
        not isinstance(raw.get("edits", []), list)
        or len(edits) != len(raw.get("edits", []))
        or any(abs(changed.count(a) - changed.count(b)) > abs(text.count(a) - text.count(b)) for a, b in ("“”", "「」"))
    )
    changed = text if bad else changed
    patch_note = {"status": "not_provided"}
    if isinstance(raw.get("patch"), dict):
        changed, patch_note = place_guarded_patch(
            changed,
            raw["patch"],
            raw=raw,
            outline=outline,
            max_chars=400,
        )
        if patch_note["status"] == "placed":
            changed = clean(changed)
    patched = patch_note["status"] == "placed"
    if detail["length_revision"] == "accepted":
        if changed == text:
            return body, dict(detail, patch=True, body_adopted=True)
        detail["length_revision"] = "local_repair_preferred"
    return changed, dict(
        detail,
        edits=[] if bad else edits,
        adoption_failed=bad,
        patch=patched,
        patch_guard=patch_note,
    )
