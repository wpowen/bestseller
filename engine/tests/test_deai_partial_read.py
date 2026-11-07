"""A multi-paragraph style read that skips paragraphs is completed by re-reading only the gaps."""

import json

from deai.flow import Session
from deai.validation import validate_response

TEXT = (
    "他把信封接过来，低头看了一眼落款。\n\n"
    "她站在门边等着，院里的雨水顺着台阶流进水沟。送信的人没有走远，还在院墙外等他回话。"
)


def fake(skip_stage, requests):
    def call(messages, *, stage, **kwargs):
        data = json.loads(messages[-1]["content"].split("\n")[-1])
        pids = [p["pid"] for p in data.get("paragraphs", [])]
        if "examples" in data:
            requests.append((stage, pids))
            checked = pids[:1] if stage == skip_stage else pids
            return dict(checked_pids=checked, issues=[])
        if "targets" in data:
            return dict(candidates=[])
        if "items" in data:
            return dict(checks=[dict(id=t["id"], ok=True, reason="fixture") for t in data["items"]])
        return dict(checked_pids=pids, facts=[], chapter={})

    return call


def test_skipped_paragraph_is_reread_instead_of_failing_the_stage():
    requests = []
    first = "recall22-initial-verb_mismatch-0"
    Session(fake(first, requests), {"_semantic_types": ["verb_mismatch"]}).classify(TEXT, "recall22-initial")
    reads = [r for r in requests if r[0].startswith(first)]
    assert reads[0] == (first, ["P1", "P2"])
    assert reads[1] == (first + "-missing0", ["P2"])
    assert not any("-format" in stage for stage, _ in reads)


def test_single_paragraph_read_still_requires_full_coverage():
    data = dict(
        paragraphs=[dict(pid="P1", sentences=[dict(sid="S1")])],
        examples=[],
        requested_type="verb_mismatch",
    )
    try:
        validate_response("x", dict(checked_pids=[], issues=[]), data)
    except ValueError:
        return
    raise AssertionError("empty coverage must be rejected when partial reads are not allowed")


def test_partial_read_rejects_issue_from_unread_paragraph():
    data = dict(
        paragraphs=[dict(pid="P1", sentences=[dict(sid="S1")]), dict(pid="P2", sentences=[dict(sid="S2")])],
        examples=[],
        requested_type="verb_mismatch",
    )
    value = dict(checked_pids=["P1"], issues=[dict(sid="S2", type="verb_mismatch", severity=2, reason="x")])
    try:
        validate_response("x", value, data, partial_coverage=True)
    except ValueError:
        return
    raise AssertionError("an issue in a paragraph the model did not read must be rejected")
