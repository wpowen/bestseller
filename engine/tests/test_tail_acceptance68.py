"""Replay saved full chapters/responses; never call a model or write a source book.

Optional TAIL68_SOURCE_ROOT selects the existing bestseller-tmp directory.
TAIL68_EVIDENCE_DIR retains read-only inputs, adopted texts and before/after inventories
in a new system-temporary directory. Missing real sources are SKIP, never fixture success.
"""

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile

import pytest

from bookengine.hook_momentum import repair_after_draft
from bookengine.q1_chapter import supplement
from bookengine.repair_additions import added_paragraph_findings, guard_repair_additions
from bookengine.repair_placement import place_tail_bounded_patch
from bookengine.story_payoffs import _apply_payoff_edits, _tail_spans
from bookengine.text import _paragraph_spans, parse_json
from tools.books_guard import compare, manifest


BOOKS = {
    "eval68": "web68-books/webui/85ab9fc7-d694-4aba-9c80-7081467bdd47",
    "eval65": "web65-books/webui/610ea95f-ecb9-4f83-ab5d-d83c31e566c5",
}
CASES = (("eval68", 12), ("eval68", 26), ("eval65", 15))
STAGES = {
    ("eval68", 12): ("momentum-payoff-repair", "momentum-payoff-recheck", "first-appearance-repair"),
    ("eval68", 26): ("finish",),
    ("eval65", 15): ("conflict-repair",),
}


def load(path):
    return json.loads(path.read_text())


def paragraphs(text):
    return [text[start:end].strip() for start, end in _paragraph_spans(text)]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("real replay is offline; network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    repository = Path(__file__).resolve().parents[2]
    source_root = Path(os.environ.get("TAIL68_SOURCE_ROOT", repository.parent / "bestseller-tmp")).resolve()
    missing = [str(source_root / book) for book in BOOKS.values() if not (source_root / book).is_dir()]
    if missing:
        pytest.skip("real replay unavailable; source directories missing: " + ", ".join(missing))
    requested = os.environ.get("TAIL68_EVIDENCE_DIR")
    root = Path(requested).resolve() if requested else tmp_path_factory.mktemp("tail68-replay")
    if requested:
        # Resolve /tmp's macOS alias; accept only a fresh system-temporary directory.
        allowed = (Path("/private/tmp"), Path(tempfile.gettempdir()).resolve())
        assert any(parent == root or parent in root.parents for parent in allowed)
        root.mkdir(exist_ok=False)
    inputs, outputs = root / "inputs", root / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    before = {name: manifest(source_root / path) for name, path in BOOKS.items()}
    write_json(root / "sources-before.json", before)
    copied = []
    for book, number in CASES:
        source, target = source_root / BOOKS[book], inputs / book
        paths = {
            "calls.json",
            f"chapter-{number:03d}.r2.draft.md",
            f"chapter-{number:03d}.body.md",
            f"final/chapter-{number:03d}.md",
            f"chapters/{number:03d}.json",
            f"writing_outlines/{number:03d}.json",
            f"steps/r2-ch{number}-draft.md",
        }
        calls = load(source / "calls.json")
        for suffix in STAGES[book, number]:
            stage = f"r2-ch{number}-{suffix}"
            paths.add(f"steps/{stage}.json")
            matches = [row for row in calls if row.get("stage") == stage and row.get("status") == "ok"]
            assert len(matches) == 1, (book, stage, "ambiguous/missing saved response")
            paths.add(f"responses/{matches[0]['seq']:03d}.json")
        for relative in sorted(paths):
            origin, destination = source / relative, target / relative
            assert origin.is_file(), f"missing real input: {origin}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copyfile(origin, destination)
            destination.chmod(0o444)
            assert origin.read_bytes() == destination.read_bytes()
            copied.append(str(origin))
    for directory in sorted((p for p in inputs.rglob("*") if p.is_dir()), reverse=True):
        directory.chmod(0o555)
    inputs.chmod(0o555)
    input_before = manifest(inputs)
    write_json(root / "inputs-before.json", input_before)
    write_json(root / "read-paths.json", sorted(set(copied)))
    try:
        yield inputs, outputs
    finally:
        after = {name: manifest(source_root / path) for name, path in BOOKS.items()}
        write_json(root / "sources-after.json", after)
        differences = {name: compare(before[name], after[name]) for name in BOOKS}
        differences["readonly_inputs"] = compare(input_before, manifest(inputs))
        write_json(root / "source-differences.json", differences)
        assert all(not changes for result in differences.values() for changes in result.values())


def draft(inputs, book, number):
    root = inputs / book
    text = (root / f"chapter-{number:03d}.r2.draft.md").read_text().strip()
    assert text == (root / f"steps/r2-ch{number}-draft.md").read_text().strip()
    return text


def response(inputs, book, number, suffix):
    root = inputs / book
    stage = f"r2-ch{number}-{suffix}"
    row = next(row for row in load(root / "calls.json") if row.get("stage") == stage and row.get("status") == "ok")
    wire = load(root / f"responses/{row['seq']:03d}.json")
    parsed = parse_json(wire["choices"][0]["message"]["content"])
    checkpoint = load(root / f"steps/{stage}.json")
    # Extractor checkpoints can add derived fields; the candidate must match the raw response exactly.
    if "patch" in parsed or "edits" in parsed:
        assert all(checkpoint.get(key) == parsed.get(key) for key in ("patch", "edits"))
    else:
        assert all(checkpoint["payoff"].get(key) == value for key, value in parsed["payoff"].items())
    return checkpoint


def adopt(outputs, name, original, candidate, placement=None):
    final, note = guard_repair_additions(original, candidate)
    path = outputs / f"{name}.adopted.md"
    path.write_text(final)
    assert path.read_text() == final
    write_json(
        outputs / f"{name}.json",
        {
            "placement": placement,
            "guard": note,
            "original_paragraphs": len(paragraphs(original)),
            "candidate_paragraphs": len(paragraphs(candidate)),
            "adopted_paragraphs": len(paragraphs(final)),
            "preserved_last": paragraphs(final)[-1] == paragraphs(original)[-1],
            "remaining_findings": added_paragraph_findings(original, final),
            "adopted_sha256": hashlib.sha256(final.encode()).hexdigest(),
        },
    )
    return final, note


def test_eval68_ch12_real_momentum_response_keeps_last_paragraph(saved):
    inputs, outputs = saved
    original = draft(inputs, "eval68", 12)
    # This actual earlier repair affected paragraph 42; replay it before the payoff insertion.
    appearance = response(inputs, "eval68", 12, "first-appearance-repair")
    current, _, reason = _apply_payoff_edits(original, _tail_spans(original), appearance, {42}, set())
    assert reason == "valid"
    patch = response(inputs, "eval68", 12, "momentum-payoff-repair")
    recheck = response(inputs, "eval68", 12, "momentum-payoff-recheck")
    observed = []

    def offline_ask(client, stage, *args, **kwargs):
        assert stage == "r2-ch12-momentum-payoff-repair"
        observed.append(stage)
        return deepcopy(patch)

    def offline_extract(client, stage, text, *args, **kwargs):
        assert stage == "r2-ch12-momentum-payoff-recheck"
        assert recheck["payoff"]["quote"] in text
        assert paragraphs(text)[recheck["payoff"]["pid"] - 1] == recheck["payoff"]["quote"]
        observed.append(stage)
        return deepcopy(recheck), False, 0

    outline = load(inputs / "eval68/writing_outlines/012.json")
    candidate, _, failed, notes = repair_after_draft(
        None,
        "r2-ch12",
        current,
        {"payoff": {}},
        False,
        {},
        {},
        outline,
        {"number": 13},
        [],
        offline_extract,
        offline_ask,
    )
    final, note = adopt(outputs, "eval68-ch12", original, candidate, notes["payoff"])
    assert not failed and notes["payoff"]["status"] == "repaired"
    assert observed == ["r2-ch12-momentum-payoff-repair", "r2-ch12-momentum-payoff-recheck"]
    assert paragraphs(final)[-2] == patch["patch"]["text"]
    assert paragraphs(final)[-1] == paragraphs(original)[-1]
    assert note["findings"] == []
    historical = (inputs / "eval68/chapter-012.body.md").read_text().strip()
    assert historical.endswith(patch["patch"]["text"])


def test_eval68_ch26_real_finish_patch_keeps_last_paragraph(saved):
    inputs, outputs = saved
    original = draft(inputs, "eval68", 26)
    patch = response(inputs, "eval68", 26, "finish")["patch"]
    candidate, accepted = supplement(original, patch)
    final, note = adopt(outputs, "eval68-ch26", original, candidate)
    assert accepted
    assert paragraphs(final)[-2] == patch["text"]
    assert paragraphs(final)[-1] == paragraphs(original)[-1]
    assert paragraphs(final)[:-2] == paragraphs(original)[:-1]
    assert note["findings"] == []


def test_eval65_ch15_real_conflict_response_restores_entire_repeated_tail(saved):
    inputs, outputs = saved
    original = draft(inputs, "eval65", 15)
    edits = response(inputs, "eval65", 15, "conflict-repair")
    candidate, applied, reason = _apply_payoff_edits(original, _tail_spans(original), edits, {119, 120}, set())
    assert reason == "valid" and applied == edits["edits"]
    # Prove this is precisely the old adopted text, not an invented approximation.
    assert candidate == (inputs / "eval65/chapter-015.body.md").read_text().strip()
    final, note = adopt(outputs, "eval65-ch15", original, candidate)
    assert note["reason"] == "repair_restates_chapter"
    assert any(row["kind"] == "copied_dialogue" for row in note["findings"])
    assert final == original
    assert paragraphs(final)[-1] == paragraphs(original)[-1]
    for line in ("所有出口，封死！", "一只苍蝇也不许飞出去！"):
        assert candidate.count(line) == 2
        assert final.count(line) == original.count(line) == 1
    assert final.count("俯身盯着那滴黑水") == original.count("俯身盯着那滴黑水") == 1
    assert added_paragraph_findings(original, final) == []


@pytest.mark.parametrize(("book", "number"), CASES)
def test_saved_writer_draft_is_unchanged_by_addition_guard(saved, book, number):
    inputs, _ = saved
    original = draft(inputs, book, number)
    assert guard_repair_additions(original, original) == (original, {"status": "checked", "findings": []})


def test_tail_rollback_preserves_an_independent_earlier_edit(saved):
    inputs, _ = saved
    original = draft(inputs, "eval65", 15)
    edits = response(inputs, "eval65", 15, "conflict-repair")
    candidate, _, _ = _apply_payoff_edits(original, _tail_spans(original), edits, {119, 120}, set())
    # A separate, deterministic punctuation edit is not part of the rejected tail block.
    first = paragraphs(original)[0]
    assert "。" in first
    changed_first = first.replace("。", "！", 1)
    candidate = candidate.replace(first, changed_first, 1)
    expected = original.replace(first, changed_first, 1)
    restored, _ = guard_repair_additions(original, candidate)
    assert restored == expected
    assert Counter(paragraphs(restored)[1:]) == Counter(paragraphs(original)[1:])


@pytest.mark.parametrize("options", [{}, {"keep_final": True}])
def test_single_paragraph_has_no_room_for_an_insertion_before_its_final(options):
    original = "门外的脚步忽然停住，他仍没有松手。"
    changed, note = place_tail_bounded_patch(original, {"after": 1, "text": "新增兑现。"}, **options)
    assert changed == original
    assert note["status"] == "invalid" and note["reason"] == "no_room_before_final"


def test_single_paragraph_explicit_replacement_keeps_the_existing_protocol():
    original = "门外的脚步忽然停住，他仍没有松手。"
    replacement = "门外的脚步突然停住，他仍没有松手。"
    changed, note = place_tail_bounded_patch(
        original, {"after": 1, "text": replacement, "replace": True}, keep_final=True
    )
    assert changed == replacement and note["status"] == "placed"
    assert note["placement"] == "requested" and note["effective_after"] == 1
